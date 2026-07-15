from accounts.models import OrganizationMembership

from .models import FRANCHISE_VENDOR_EDITABLE_FIELDS, FranchiseUpdateRequest


def user_can_manage_franchise(user, franchise):
    if not user or not user.is_authenticated or not franchise or not franchise.organization_id:
        return False
    if user.is_staff:
        return True
    return OrganizationMembership.objects.filter(
        user=user,
        organization=franchise.organization,
        organization__status="active",
        is_active=True,
    ).exists()


def create_update_request_from_franchise(franchise, user):
    if not franchise.organization_id:
        raise ValueError("Franchise must be assigned to an organization before vendor edits.")

    update_request = FranchiseUpdateRequest(
        franchise=franchise,
        organization=franchise.organization,
        submitted_by=user if user and user.is_authenticated else None,
        status=FranchiseUpdateRequest.STATUS_DRAFT,
    )
    for field_name in FRANCHISE_VENDOR_EDITABLE_FIELDS:
        if hasattr(franchise, field_name) and hasattr(update_request, field_name):
            setattr(update_request, field_name, getattr(franchise, field_name))
    update_request.save()
    return update_request
