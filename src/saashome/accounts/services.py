from franchises.models import Franchise

from .models import Organization


def get_user_organizations(user):
    if not user or not user.is_authenticated:
        return Organization.objects.none()

    if user.is_staff:
        return Organization.objects.filter(status=Organization.STATUS_ACTIVE)

    return Organization.objects.filter(
        memberships__user=user,
        memberships__is_active=True,
        status=Organization.STATUS_ACTIVE,
    ).distinct()


def get_user_franchises(user):
    organizations = get_user_organizations(user)
    return Franchise.objects.filter(
        organization__in=organizations,
        organization__status=Organization.STATUS_ACTIVE,
        is_active=True,
    ).select_related("category", "organization")
