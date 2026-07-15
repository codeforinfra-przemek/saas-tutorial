from .permissions import get_access_role_label, has_active_vendor_membership


def access_context(request):
    return {
        "access_role_label": get_access_role_label(request.user),
        "can_access_vendor_portal": has_active_vendor_membership(request.user),
    }
