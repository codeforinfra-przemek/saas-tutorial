from collections import Counter
from datetime import timedelta

from django.db.models import Count, Max
from django.utils import timezone

from accounts.services import get_user_franchises, get_user_organizations
from billing.services import organization_has_feature
from franchises.models import Franchise
from leads.models import Lead
from visits.models import Visit


RANGE_DAYS = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
}


def get_date_range(range_key):
    normalized_key = range_key if range_key in RANGE_DAYS else "30d"
    end_date = timezone.now()
    start_date = end_date - timedelta(days=RANGE_DAYS[normalized_key])
    return start_date, end_date, normalized_key


def safe_percentage(numerator, denominator):
    if not denominator:
        return 0
    return round((numerator / denominator) * 100, 2)


def get_lead_budget_bucket(value):
    if value is None:
        return "Unknown"
    if value < 50000:
        return "0-50k"
    if value < 100000:
        return "50-100k"
    if value < 250000:
        return "100-250k"
    if value < 500000:
        return "250-500k"
    return "500k+"


def lead_status_breakdown(leads):
    return list(
        leads.values("status")
        .annotate(count=Count("id"))
        .order_by("-count", "status")
    )


def traffic_source_breakdown(visits):
    rows = visits.values("utm_source").annotate(count=Count("id")).order_by("-count", "utm_source")
    return [
        {"source": row["utm_source"] or "Direct/unknown", "count": row["count"]}
        for row in rows
    ]


def leads_city_breakdown(leads):
    rows = leads.values("city").annotate(count=Count("id")).order_by("-count", "city")[:12]
    return [{"city": row["city"] or "Unknown", "count": row["count"]} for row in rows]


def leads_budget_breakdown(leads):
    counter = Counter()
    for value in leads.values_list("investment_budget", flat=True):
        counter[get_lead_budget_bucket(value)] += 1
    order = ["0-50k", "50-100k", "100-250k", "250-500k", "500k+", "Unknown"]
    return [{"bucket": bucket, "count": counter[bucket]} for bucket in order if counter[bucket]]


def get_vendor_analytics(user, range_key="30d"):
    start_date, end_date, normalized_key = get_date_range(range_key)
    organizations = list(get_user_organizations(user))
    analytics_enabled_organizations = [
        organization for organization in organizations if organization_has_feature(organization, "can_view_analytics")
    ]
    analytics_locked_organizations = [
        organization for organization in organizations if organization not in analytics_enabled_organizations
    ]

    if not analytics_enabled_organizations:
        return {
            "range_key": normalized_key,
            "start_date": start_date,
            "end_date": end_date,
            "analytics_enabled_organizations": analytics_enabled_organizations,
            "analytics_locked_organizations": analytics_locked_organizations,
            "can_view_analytics": False,
            "franchises": Franchise.objects.none(),
            "visits_count": 0,
            "leads_count": 0,
            "conversion_rate": 0,
            "franchise_rows": [],
            "status_breakdown": [],
            "traffic_sources": [],
            "leads_by_city": [],
            "leads_by_budget": [],
        }

    franchises = get_user_franchises(user).filter(organization__in=analytics_enabled_organizations)
    franchise_ids = list(franchises.values_list("id", flat=True))
    visits = Visit.objects.filter(
        franchise_id__in=franchise_ids,
        page_type=Visit.PAGE_TYPE_FRANCHISE_DETAIL,
        created_at__gte=start_date,
        created_at__lte=end_date,
    )
    leads = Lead.objects.filter(
        franchise_id__in=franchise_ids,
        created_at__gte=start_date,
        created_at__lte=end_date,
    ).select_related("franchise")

    visits_by_franchise = {
        row["franchise_id"]: row["count"]
        for row in visits.values("franchise_id").annotate(count=Count("id"))
    }
    lead_rows = leads.values("franchise_id").annotate(
        count=Count("id"),
        last_lead_at=Max("created_at"),
    )
    leads_by_franchise = {row["franchise_id"]: row for row in lead_rows}
    new_by_franchise = {
        row["franchise_id"]: row["count"]
        for row in leads.filter(status=Lead.STATUS_NEW).values("franchise_id").annotate(count=Count("id"))
    }
    qualified_by_franchise = {
        row["franchise_id"]: row["count"]
        for row in leads.filter(status=Lead.STATUS_QUALIFIED).values("franchise_id").annotate(count=Count("id"))
    }

    franchise_rows = []
    for franchise in franchises:
        views_count = visits_by_franchise.get(franchise.id, 0)
        lead_count = leads_by_franchise.get(franchise.id, {}).get("count", 0)
        franchise_rows.append(
            {
                "franchise": franchise,
                "views_count": views_count,
                "leads_count": lead_count,
                "conversion_rate": safe_percentage(lead_count, views_count),
                "new_leads_count": new_by_franchise.get(franchise.id, 0),
                "qualified_leads_count": qualified_by_franchise.get(franchise.id, 0),
                "last_lead_at": leads_by_franchise.get(franchise.id, {}).get("last_lead_at"),
            }
        )
    franchise_rows.sort(key=lambda row: (row["leads_count"], row["views_count"]), reverse=True)

    visits_count = visits.count()
    leads_count = leads.count()
    return {
        "range_key": normalized_key,
        "start_date": start_date,
        "end_date": end_date,
        "analytics_enabled_organizations": analytics_enabled_organizations,
        "analytics_locked_organizations": analytics_locked_organizations,
        "can_view_analytics": True,
        "franchises": franchises,
        "visits_count": visits_count,
        "leads_count": leads_count,
        "conversion_rate": safe_percentage(leads_count, visits_count),
        "franchise_rows": franchise_rows,
        "status_breakdown": lead_status_breakdown(leads),
        "traffic_sources": traffic_source_breakdown(visits),
        "leads_by_city": leads_city_breakdown(leads),
        "leads_by_budget": leads_budget_breakdown(leads),
    }


def get_admin_analytics(range_key="30d"):
    start_date, end_date, normalized_key = get_date_range(range_key)
    visits = Visit.objects.filter(
        page_type=Visit.PAGE_TYPE_FRANCHISE_DETAIL,
        created_at__gte=start_date,
        created_at__lte=end_date,
    )
    leads = Lead.objects.filter(created_at__gte=start_date, created_at__lte=end_date)
    visits_count = visits.count()
    leads_count = leads.count()

    return {
        "range_key": normalized_key,
        "start_date": start_date,
        "end_date": end_date,
        "visits_count": visits_count,
        "leads_count": leads_count,
        "conversion_rate": safe_percentage(leads_count, visits_count),
        "top_franchises_by_views": list(
            visits.values("franchise__name")
            .annotate(count=Count("id"))
            .order_by("-count", "franchise__name")[:10]
        ),
        "top_franchises_by_leads": list(
            leads.values("franchise__name")
            .annotate(count=Count("id"))
            .order_by("-count", "franchise__name")[:10]
        ),
        "top_categories_by_leads": list(
            leads.values("franchise__category__name")
            .annotate(count=Count("id"))
            .order_by("-count", "franchise__category__name")[:10]
        ),
        "top_organizations_by_leads": list(
            leads.values("franchise__organization__name")
            .annotate(count=Count("id"))
            .order_by("-count", "franchise__organization__name")[:10]
        ),
        "status_breakdown": lead_status_breakdown(leads),
        "traffic_sources": traffic_source_breakdown(visits),
    }
