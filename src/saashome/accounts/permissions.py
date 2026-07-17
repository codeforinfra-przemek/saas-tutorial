from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Organization, OrganizationMembership


def has_active_vendor_membership(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    return OrganizationMembership.objects.filter(
        user=user,
        is_active=True,
        organization__status=Organization.STATUS_ACTIVE,
    ).exists()


def can_manage_franchise_billing(user, franchise):
    if not user or not user.is_authenticated or not franchise or not franchise.organization_id:
        return False
    if user.is_staff:
        return True
    return OrganizationMembership.objects.filter(
        user=user,
        organization_id=franchise.organization_id,
        organization__status=Organization.STATUS_ACTIVE,
        is_active=True,
        role=OrganizationMembership.ROLE_OWNER,
    ).exists()


def can_manage_franchise_profile(user, franchise):
    if not user or not user.is_authenticated or not franchise or not franchise.organization_id:
        return False
    if user.is_staff:
        return True
    return OrganizationMembership.objects.filter(
        user=user,
        organization_id=franchise.organization_id,
        organization__status=Organization.STATUS_ACTIVE,
        is_active=True,
        role__in=(OrganizationMembership.ROLE_OWNER, OrganizationMembership.ROLE_ADMIN),
    ).exists()


def get_access_role_label(user):
    if not user or not user.is_authenticated:
        return "Gość"
    if user.is_staff:
        return "Administrator"
    roles = set(
        OrganizationMembership.objects.filter(
            user=user,
            is_active=True,
            organization__status=Organization.STATUS_ACTIVE,
        )
        .values_list("role", flat=True)
    )
    for role, label in (
        (OrganizationMembership.ROLE_OWNER, "Owner"),
        (OrganizationMembership.ROLE_ADMIN, "Admin organizacji"),
        (OrganizationMembership.ROLE_MEMBER, "Member"),
    ):
        if role in roles:
            return label
    return "Użytkownik"


def staff_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


def vendor_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not has_active_vendor_membership(request.user):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper
