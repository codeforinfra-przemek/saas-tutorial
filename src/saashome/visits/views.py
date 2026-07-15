from django.shortcuts import render

from accounts.permissions import staff_required

from .models import Visit


@staff_required
def visit_list_view(request):
    visits = Visit.objects.select_related("user", "franchise").prefetch_related("events")[:100]
    context = {
        "site_name": "SaaS Home",
        "page_title": "Wizyty",
        "active_page": "visits",
        "visits": visits,
    }
    return render(request, "visits/visit_list.html", context)
