import logging
from smtplib import SMTPException

from django.conf import settings
from django.core.mail import BadHeaderError, send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import Organization, OrganizationMembership, UserProfile

from .models import ClaimProfileRequest


logger = logging.getLogger(__name__)


def make_unique_slug(model, value):
    base_slug = slugify(value) or "organization"
    slug = base_slug
    number = 2
    while model.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{number}"
        number += 1
    return slug


def get_existing_or_create_organization_for_claim(claim):
    if claim.organization_id:
        return claim.organization
    return Organization.objects.create(
        name=claim.company_name,
        slug=make_unique_slug(Organization, claim.company_name),
        website_url=claim.company_website,
        contact_email=claim.company_email or claim.claimant_email,
        billing_email=claim.company_email or claim.claimant_email,
        status=Organization.STATUS_ACTIVE,
    )


@transaction.atomic
def approve_claim_request(claim, reviewed_by=None, organization=None):
    claim = ClaimProfileRequest.objects.select_for_update().select_related("franchise", "organization", "user").get(pk=claim.pk)
    if claim.status == ClaimProfileRequest.STATUS_APPROVED:
        return claim
    if claim.status not in (ClaimProfileRequest.STATUS_NEW, ClaimProfileRequest.STATUS_IN_REVIEW):
        return claim

    organization = organization or get_existing_or_create_organization_for_claim(claim)
    if claim.user_id:
        OrganizationMembership.objects.update_or_create(
            organization=organization,
            user=claim.user,
            defaults={"role": OrganizationMembership.ROLE_OWNER, "is_active": True},
        )
        profile, _ = UserProfile.objects.get_or_create(user=claim.user)
        if profile.user_type != UserProfile.USER_TYPE_VENDOR:
            profile.user_type = UserProfile.USER_TYPE_VENDOR
            profile.save(update_fields=["user_type", "updated_at"])

    franchise = claim.franchise
    franchise.organization = organization
    franchise.is_verified = True
    franchise.save(update_fields=["organization", "is_verified", "updated_at"])

    now = timezone.now()
    claim.status = ClaimProfileRequest.STATUS_APPROVED
    claim.organization = organization
    claim.reviewed_by = reviewed_by
    claim.reviewed_at = now
    claim.approved_at = now
    claim.save(update_fields=["status", "organization", "reviewed_by", "reviewed_at", "approved_at", "updated_at"])
    return claim


def reject_claim_request(claim, reviewed_by=None, feedback=""):
    return claim.reject(reviewed_by=reviewed_by, feedback=feedback)


def notify_new_claim_request(claim, request=None):
    recipient = getattr(settings, "CLAIMS_NOTIFICATION_EMAIL", "")
    if not recipient:
        return False
    subject = f"New franchise claim: {claim.franchise.name}"
    body = (
        f"Franchise: {claim.franchise.name}\nClaimant: {claim.claimant_name}\n"
        f"Email: {claim.claimant_email}\nCompany: {claim.company_name}\n\n"
        f"Message:\n{claim.message or '-'}\n"
    )
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
    except (BadHeaderError, OSError, SMTPException):
        logger.exception("Could not send claim notification email.")
        return False
    return True
