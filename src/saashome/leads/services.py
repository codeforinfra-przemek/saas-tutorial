import logging
from smtplib import SMTPException

from django.conf import settings
from django.core.mail import BadHeaderError, send_mail
from django.utils import timezone

from accounts.services import get_user_franchises
from billing.services import franchise_has_feature

from .models import Lead, LeadActivity


logger = logging.getLogger(__name__)


def get_vendor_leads_for_user(user):
    franchises = get_user_franchises(user)
    return (
        Lead.objects.filter(franchise__in=franchises)
        .select_related("franchise", "franchise__organization", "visit")
        .order_by("-created_at")
    )


def create_lead_activity(
    lead,
    activity_type,
    user=None,
    old_status="",
    new_status="",
    note="",
    metadata=None,
):
    activity = LeadActivity.objects.create(
        lead=lead,
        activity_type=activity_type,
        created_by=user if user and user.is_authenticated else None,
        old_status=old_status or "",
        new_status=new_status or "",
        note=note or "",
        metadata=metadata or {},
    )
    lead.last_activity_at = timezone.now()
    lead.save(update_fields=["last_activity_at", "updated_at"])
    return activity


def change_lead_status(lead, new_status, user=None, note="", rejected_reason=""):
    old_status = lead.status
    lead.status = new_status
    update_fields = ["status", "updated_at"]

    if new_status == Lead.STATUS_CONTACTED:
        lead.contacted_at = timezone.now()
        update_fields.append("contacted_at")
    elif new_status == Lead.STATUS_QUALIFIED:
        lead.qualified_at = timezone.now()
        update_fields.append("qualified_at")
    elif new_status == Lead.STATUS_REJECTED:
        lead.rejected_at = timezone.now()
        lead.rejected_reason = rejected_reason or lead.rejected_reason
        update_fields.extend(["rejected_at", "rejected_reason"])

    lead.save(update_fields=update_fields)
    return create_lead_activity(
        lead,
        LeadActivity.TYPE_STATUS_CHANGED,
        user=user,
        old_status=old_status,
        new_status=new_status,
        note=note,
        metadata={"rejected_reason": rejected_reason} if rejected_reason else {},
    )


def add_lead_note(lead, note, user=None):
    note = (note or "").strip()
    if not note:
        return None
    lead.vendor_notes = f"{lead.vendor_notes}\n\n{note}".strip()
    lead.save(update_fields=["vendor_notes", "updated_at"])
    return create_lead_activity(lead, LeadActivity.TYPE_NOTE_ADDED, user=user, note=note)


def notify_new_lead(lead, request=None):
    recipients = []
    admin_email = getattr(settings, "LEADS_NOTIFICATION_EMAIL", "")
    if admin_email:
        recipients.append(admin_email)

    organization = getattr(lead.franchise, "organization", None)
    if (
        organization
        and organization.contact_email
        and franchise_has_feature(lead.franchise, "can_view_leads")
    ):
        recipients.append(organization.contact_email)

    recipients = list(dict.fromkeys([recipient for recipient in recipients if recipient]))
    if not recipients:
        return

    priority = franchise_has_feature(lead.franchise, "can_receive_priority_leads")
    subject_prefix = "[PRIORYTET] " if priority else ""
    subject = f"{subject_prefix}New franchise lead: {lead.franchise.name}"
    body = (
        f"Franchise: {lead.franchise.name}\n"
        f"Name: {lead.name}\n"
        f"Email: {lead.email}\n"
        f"Phone: {lead.phone}\n"
        f"City: {lead.city or '-'}\n"
        f"Investment budget: {lead.investment_budget or '-'}\n"
        f"Source: {lead.source_path or '-'}\n\n"
        f"Message:\n{lead.message or '-'}\n"
    )

    try:
        send_mail(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            recipients,
            fail_silently=False,
        )
    except (BadHeaderError, OSError, SMTPException) as exc:
        logger.exception("Could not send lead notification email.")
        create_lead_activity(
            lead,
            LeadActivity.TYPE_EMAIL_NOTIFICATION_FAILED,
            metadata={"recipients": recipients, "error": str(exc)},
        )
        return

    create_lead_activity(
        lead,
        LeadActivity.TYPE_EMAIL_NOTIFICATION_SENT,
        metadata={"recipients": recipients, "priority": priority},
    )
