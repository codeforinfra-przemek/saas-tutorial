from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import render
from django.utils import timezone

from accounts.services import get_user_franchises, get_user_organizations
from leads.models import Lead
from visits.models import Visit


def percent(numerator, denominator):
    if not denominator:
        return 0
    return round((numerator / denominator) * 100, 1)


@login_required
def vendor_dashboard_view(request):
    organizations = get_user_organizations(request.user)
    franchises = get_user_franchises(request.user)
    franchise_ids = list(franchises.values_list("id", flat=True))
    since_30d = timezone.now() - timedelta(days=30)

    visits = Visit.objects.filter(franchise_id__in=franchise_ids)
    leads = Lead.objects.filter(franchise_id__in=franchise_ids)

    visits_30d = visits.filter(created_at__gte=since_30d)
    leads_30d = leads.filter(created_at__gte=since_30d)

    visits_30d_by_franchise = {
        row["franchise_id"]: row["count"]
        for row in visits_30d.values("franchise_id").annotate(count=Count("id"))
    }
    leads_30d_by_franchise = {
        row["franchise_id"]: row["count"]
        for row in leads_30d.values("franchise_id").annotate(count=Count("id"))
    }

    franchise_rows = []
    for franchise in franchises:
        views_count = visits_30d_by_franchise.get(franchise.id, 0)
        leads_count = leads_30d_by_franchise.get(franchise.id, 0)
        franchise_rows.append(
            {
                "franchise": franchise,
                "views_30d": views_count,
                "leads_30d": leads_count,
                "conversion_30d": percent(leads_count, views_count),
            }
        )

    visits_30d_count = visits_30d.count()
    leads_30d_count = leads_30d.count()

    context = {
        "site_name": "SaaS Home",
        "page_title": "Vendor dashboard",
        "active_page": "vendor",
        "organizations": organizations,
        "franchises": franchises,
        "visits_30d_count": visits_30d_count,
        "leads_30d_count": leads_30d_count,
        "all_visits_count": visits.count(),
        "all_leads_count": leads.count(),
        "conversion_rate_30d": percent(leads_30d_count, visits_30d_count),
        "franchise_rows": franchise_rows,
        "recent_leads": leads.select_related("franchise").order_by("-created_at")[:10],
    }
    return render(request, "vendor/dashboard.html", context)
