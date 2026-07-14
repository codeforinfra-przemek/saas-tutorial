from franchises.models import Franchise

from .models import Organization


def get_user_organizations(user):
    if not user or not user.is_authenticated:
        return Organization.objects.none()

    return Organization.objects.filter(
        memberships__user=user,
        memberships__is_active=True,
        status=Organization.STATUS_ACTIVE,
    ).distinct()


def get_user_franchises(user):
    organizations = get_user_organizations(user)
    if not organizations.exists():
        return Franchise.objects.none()

    return Franchise.objects.filter(
        organization__in=organizations,
        organization__status=Organization.STATUS_ACTIVE,
        is_active=True,
    ).select_related("category", "organization")
