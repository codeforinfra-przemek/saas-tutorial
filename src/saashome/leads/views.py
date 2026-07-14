import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib import messages
from django.core.mail import BadHeaderError, send_mail
from django.shortcuts import get_object_or_404, redirect

from franchises.models import Franchise
from visits.models import Visit, VisitEvent
from visits.services import ensure_session_key, get_client_ip, hash_ip

from .forms import LeadForm


logger = logging.getLogger(__name__)


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
