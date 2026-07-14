from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .models import Visit


@login_required
def visit_list_view(request):
    if not request.user.is_staff:
        raise PermissionDenied

    visits = Visit.objects.select_related("user")[:100]
    context = {
        "site_name": "SaaS Home",
        "page_title": "Wizyty",
        "active_page": "visits",
        "visits": visits,
    }
    return render(request, "visits/visit_list.html", context)
