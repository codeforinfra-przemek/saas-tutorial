from accounts.permissions import can_manage_franchise_billing

from .models import FRANCHISE_VENDOR_EDITABLE_FIELDS, FranchiseUpdateRequest


def user_can_manage_franchise(user, franchise):
    return can_manage_franchise_billing(user, franchise)


def create_update_request_from_franchise(franchise, user, save=True):
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
    if save:
        update_request.save()
    return update_request
