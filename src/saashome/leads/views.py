import hashlib
import hmac
import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib import messages
from django.core.mail import BadHeaderError, send_mail
from django.shortcuts import get_object_or_404, redirect

from franchises.models import Franchise

from .forms import LeadForm


logger = logging.getLogger(__name__)


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def hash_ip(ip_address):
    if not ip_address:
        return ""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        ip_address.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


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

    if not request.session.session_key:
        request.session.create()

    lead = form.save(commit=False)
    lead.franchise = franchise
    if request.user.is_authenticated:
        lead.user = request.user
    lead.session_key = request.session.session_key or ""
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

    try:
        send_lead_notification(lead)
    except (BadHeaderError, OSError, SMTPException):
        logger.exception("Could not send lead notification email.")

    messages.success(
        request,
        "Dziękujemy. Zapisaliśmy Twoje zgłoszenie i wrócimy z informacjami o tej franczyzie.",
    )
    return redirect(franchise.get_absolute_url() + "#request-info")
