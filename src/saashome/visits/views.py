from django.db.models import Q
from django.shortcuts import render

from accounts.permissions import staff_required
from franchises.models import Franchise

from .models import Visit


@staff_required
def visit_list_view(request):
    visits = Visit.objects.select_related("user", "franchise").prefetch_related("events")
    q = request.GET.get("q", "").strip()
    franchise_id = request.GET.get("franchise", "").strip()
    page_type = request.GET.get("page_type", "").strip()
    if q:
        visits = visits.filter(
            Q(path__icontains=q)
            | Q(full_path__icontains=q)
            | Q(franchise__name__icontains=q)
            | Q(session_key__icontains=q)
        )
    if franchise_id:
        visits = visits.filter(franchise_id=franchise_id)
    if page_type:
        visits = visits.filter(page_type=page_type)
    context = {
        "site_name": "SaaS Home",
        "page_title": "Wizyty",
        "active_page": "visits",
        "visits": visits[:200],
        "franchises": Franchise.objects.order_by("name"),
        "page_type_choices": Visit.PAGE_TYPE_CHOICES,
        "filters": {"q": q, "franchise": franchise_id, "page_type": page_type},
    }
    return render(request, "visits/visit_list.html", context)
