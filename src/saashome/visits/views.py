from django.shortcuts import render

from .models import Visit


def visit_list_view(request):
    visits = Visit.objects.select_related("user")[:100]
    context = {
        "site_name": "SaaS Home",
        "page_title": "Wizyty",
        "active_page": "visits",
        "visits": visits,
    }
    return render(request, "visits/visit_list.html", context)
