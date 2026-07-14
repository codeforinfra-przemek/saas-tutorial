import logging
from smtplib import SMTPException
from functools import wraps
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import BadHeaderError, send_mail
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from franchises.models import Franchise
from visits.models import Visit, VisitEvent
from visits.services import ensure_session_key, get_client_ip, hash_ip

from .forms import LeadForm, LeadManagementForm
from .models import Lead


logger = logging.getLogger(__name__)


def lead_management_context(**kwargs):
    context = {
        "active_page": "leads",
        "site_name": "Porównaj Franczyzę",
    }
    context.update(kwargs)
    return context


def staff_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def send_lead_notification(lead):
    recipient = getattr(settings, "LEADS_NOTIFICATION_EMAIL", "")
    if not recipient:
        return

    subject = f"New franchise lead: {lead.franchise.name}"
    body = (
        f"Franchise: {lead.franchise.name}\n"
        f"Name: {lead.name}\n"
        f"Email: {lead.email}\n"
        f"Phone: {lead.phone}\n"
        f"City: {lead.city}\n"
        f"Investment budget: {lead.investment_budget or 'not provided'}\n\n"
        f"Message:\n{lead.message or '-'}\n"
    )
    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
        fail_silently=False,
    )


def get_related_visit(request, franchise):
    session_key = ensure_session_key(request)
    visit_id = request.session.get("last_franchise_visit_id")
    if visit_id:
        visit = Visit.objects.filter(
            id=visit_id,
            franchise=franchise,
            session_key=session_key,
        ).first()
        if visit:
            return visit

    return (
        Visit.objects.filter(
            session_key=session_key,
            franchise=franchise,
            page_type=Visit.PAGE_TYPE_FRANCHISE_DETAIL,
        )
        .order_by("-created_at")
        .first()
    )


def create_lead_view(request, slug):
    franchise = get_object_or_404(Franchise, slug=slug, is_active=True)

    if request.method != "POST":
        return redirect(franchise.get_absolute_url())

    form = LeadForm(request.POST)
    if not form.is_valid():
        request.session["lead_form_errors"] = form.errors.get_json_data()
        request.session["lead_form_data"] = {
            key: value
            for key, value in request.POST.items()
            if key not in ("csrfmiddlewaretoken", "website")
        }
        messages.error(request, "Sprawdź formularz kontaktowy i spróbuj ponownie.")
        return redirect(franchise.get_absolute_url() + "#request-info")

    session_key = ensure_session_key(request)
    related_visit = get_related_visit(request, franchise)

    lead = form.save(commit=False)
    lead.franchise = franchise
    lead.visit = related_visit
    if request.user.is_authenticated:
        lead.user = request.user
    lead.session_key = session_key
    lead.source_path = request.get_full_path()
    lead.referrer = request.META.get("HTTP_REFERER", "")
    lead.user_agent = request.META.get("HTTP_USER_AGENT", "")
    lead.ip_hash = hash_ip(get_client_ip(request))
    lead.utm_source = request.GET.get("utm_source", request.POST.get("utm_source", ""))
    lead.utm_medium = request.GET.get("utm_medium", request.POST.get("utm_medium", ""))
    lead.utm_campaign = request.GET.get("utm_campaign", request.POST.get("utm_campaign", ""))
    lead.utm_content = request.GET.get("utm_content", request.POST.get("utm_content", ""))
    lead.utm_term = request.GET.get("utm_term", request.POST.get("utm_term", ""))
    lead.save()

    if related_visit:
        VisitEvent.objects.create(
            visit=related_visit,
            event_type=VisitEvent.EVENT_SUBMIT_LEAD_FORM,
            value=lead.email,
            metadata={"lead_id": lead.id},
        )

    try:
        send_lead_notification(lead)
    except (BadHeaderError, OSError, SMTPException):
        logger.exception("Could not send lead notification email.")

    messages.success(
        request,
        "Dziękujemy. Zapisaliśmy Twoje zgłoszenie i wrócimy z informacjami o tej franczyzie.",
    )
    return redirect(franchise.get_absolute_url() + "#request-info")


@staff_required
def lead_list_view(request):
    leads = Lead.objects.select_related("franchise", "user", "visit")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    franchise_id = request.GET.get("franchise", "").strip()

    if q:
        leads = leads.filter(
            Q(name__icontains=q)
            | Q(email__icontains=q)
            | Q(phone__icontains=q)
            | Q(city__icontains=q)
            | Q(franchise__name__icontains=q)
        )
    if status:
        leads = leads.filter(status=status)
    if franchise_id:
        leads = leads.filter(franchise_id=franchise_id)

    thirty_days_ago = timezone.now() - timedelta(days=30)
    base_leads = Lead.objects.all()
    stats = {
        "total": base_leads.count(),
        "new": base_leads.filter(status=Lead.STATUS_NEW).count(),
        "last_30d": base_leads.filter(created_at__gte=thirty_days_ago).count(),
        "qualified": base_leads.filter(status=Lead.STATUS_QUALIFIED).count(),
    }

    franchise_stats = (
        base_leads.values("franchise__name")
        .annotate(total=Count("id"))
        .order_by("-total", "franchise__name")[:8]
    )

    context = lead_management_context(
        page_title="Leady",
        leads=leads[:200],
        stats=stats,
        franchise_stats=franchise_stats,
        status_choices=Lead.STATUS_CHOICES,
        franchises=Franchise.objects.order_by("name"),
        filters={
            "q": q,
            "status": status,
            "franchise": franchise_id,
        },
    )
    return render(request, "leads/list.html", context)


@staff_required
def lead_detail_view(request, pk):
    lead = get_object_or_404(
        Lead.objects.select_related("franchise", "user", "visit"),
        pk=pk,
    )
    visit_events = VisitEvent.objects.none()
    if lead.visit_id:
        visit_events = lead.visit.events.order_by("-created_at")[:20]

    context = lead_management_context(
        page_title=f"Lead: {lead.name}",
        lead=lead,
        visit_events=visit_events,
    )
    return render(request, "leads/detail.html", context)


@staff_required
def lead_create_view(request):
    form = LeadManagementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        lead = form.save(commit=False)
        lead.source_path = request.path
        lead.referrer = request.META.get("HTTP_REFERER", "")
        lead.user_agent = request.META.get("HTTP_USER_AGENT", "")
        lead.ip_hash = hash_ip(get_client_ip(request))
        lead.session_key = ensure_session_key(request)
        lead.save()
        messages.success(request, "Lead został dodany.")
        return redirect("leads:detail", pk=lead.pk)

    context = lead_management_context(
        page_title="Nowy lead",
        form=form,
        form_title="Dodaj lead",
        submit_label="Dodaj lead",
    )
    return render(request, "leads/form.html", context)


@staff_required
def lead_edit_view(request, pk):
    lead = get_object_or_404(Lead.objects.select_related("franchise"), pk=pk)
    form = LeadManagementForm(request.POST or None, instance=lead)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Lead został zaktualizowany.")
        return redirect("leads:detail", pk=lead.pk)

    context = lead_management_context(
        page_title=f"Edytuj lead: {lead.name}",
        form=form,
        lead=lead,
        form_title="Edytuj lead",
        submit_label="Zapisz zmiany",
    )
    return render(request, "leads/form.html", context)


@staff_required
def lead_delete_view(request, pk):
    lead = get_object_or_404(Lead.objects.select_related("franchise"), pk=pk)
    if request.method == "POST":
        messages.success(request, f"Lead {lead.name} został usunięty.")
        lead.delete()
        return redirect("leads:list")

    context = lead_management_context(
        page_title=f"Usuń lead: {lead.name}",
        lead=lead,
    )
    return render(request, "leads/confirm_delete.html", context)
