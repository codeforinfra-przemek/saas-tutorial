from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.services import get_user_franchises, get_user_organizations
from billing.services import get_active_subscription, get_organization_plan
from franchises.forms import FranchiseUpdateRequestForm
from franchises.models import FranchiseUpdateRequest
from franchises.services import create_update_request_from_franchise
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

    organization_rows = []
    lead_enabled_org_ids = set()
    analytics_enabled_org_ids = set()
    for organization in organizations:
        subscription = get_active_subscription(organization)
        plan = subscription.plan if subscription else get_organization_plan(organization)
        features = {
            "can_view_leads": bool(plan and plan.can_view_leads),
            "can_view_analytics": bool(plan and plan.can_view_analytics),
            "can_show_website": bool(plan and plan.can_show_website),
            "can_show_documents": bool(plan and plan.can_show_documents),
            "can_be_verified": bool(plan and plan.can_be_verified),
            "can_be_promoted": bool(plan and plan.can_be_promoted),
        }
        if features["can_view_leads"]:
            lead_enabled_org_ids.add(organization.id)
        if features["can_view_analytics"]:
            analytics_enabled_org_ids.add(organization.id)
        organization_rows.append(
            {
                "organization": organization,
                "subscription": subscription,
                "plan": plan,
                "features": features,
            }
        )

    lead_contact_franchise_ids = [
        franchise.id for franchise in franchises if franchise.organization_id in lead_enabled_org_ids
    ]
    analytics_franchise_ids = [
        franchise.id for franchise in franchises if franchise.organization_id in analytics_enabled_org_ids
    ]

    visits = Visit.objects.filter(franchise_id__in=franchise_ids)
    leads = Lead.objects.filter(franchise_id__in=franchise_ids)
    analytics_visits = visits.filter(franchise_id__in=analytics_franchise_ids)

    visits_30d = analytics_visits.filter(created_at__gte=since_30d)
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
        can_view_analytics = franchise.organization_id in analytics_enabled_org_ids
        can_view_leads = franchise.organization_id in lead_enabled_org_ids
        views_count = visits_30d_by_franchise.get(franchise.id, 0) if can_view_analytics else None
        leads_count = leads_30d_by_franchise.get(franchise.id, 0)
        franchise_rows.append(
            {
                "franchise": franchise,
                "views_30d": views_count,
                "leads_30d": leads_count,
                "conversion_30d": percent(leads_count, views_count) if can_view_analytics else None,
                "can_view_analytics": can_view_analytics,
                "can_view_leads": can_view_leads,
            }
        )

    visits_30d_count = visits_30d.count()
    leads_30d_count = leads_30d.count()
    all_visits_count = analytics_visits.count()
    all_leads_count = leads.count()

    recent_leads = list(leads.select_related("franchise").order_by("-created_at")[:10])
    for lead in recent_leads:
        lead.can_view_contact = lead.franchise_id in lead_contact_franchise_ids

    context = {
        "site_name": "SaaS Home",
        "page_title": "Vendor dashboard",
        "active_page": "vendor",
        "organizations": organizations,
        "organization_rows": organization_rows,
        "franchises": franchises,
        "visits_30d_count": visits_30d_count,
        "leads_30d_count": leads_30d_count,
        "all_visits_count": all_visits_count,
        "all_leads_count": all_leads_count,
        "conversion_rate_30d": percent(leads_30d_count, visits_30d_count),
        "franchise_rows": franchise_rows,
        "recent_leads": recent_leads,
        "can_view_any_lead_contacts": bool(lead_enabled_org_ids),
        "can_view_any_analytics": bool(analytics_enabled_org_ids),
        "lead_contact_franchise_ids": lead_contact_franchise_ids,
        "pending_update_requests_count": FranchiseUpdateRequest.objects.filter(
            franchise_id__in=franchise_ids,
            status=FranchiseUpdateRequest.STATUS_SUBMITTED,
        ).count(),
    }
    return render(request, "vendor/dashboard.html", context)


@login_required
def vendor_franchise_list_view(request):
    franchises = list(get_user_franchises(request.user))
    latest_updates = {}
    update_requests = FranchiseUpdateRequest.objects.filter(
        franchise__in=franchises,
    ).select_related("franchise").order_by("franchise_id", "-updated_at")
    for update_request in update_requests:
        latest_updates.setdefault(update_request.franchise_id, update_request)

    rows = []
    for franchise in franchises:
        rows.append(
            {
                "franchise": franchise,
                "latest_update": latest_updates.get(franchise.id),
            }
        )

    context = {
        "site_name": "SaaS Home",
        "page_title": "My franchises",
        "active_page": "vendor",
        "rows": rows,
    }
    return render(request, "vendor/franchises/list.html", context)


@login_required
def vendor_franchise_edit_view(request, slug):
    franchises = get_user_franchises(request.user)
    franchise = get_object_or_404(franchises, slug=slug)
    organization = franchise.organization

    submitted_request = FranchiseUpdateRequest.objects.filter(
        franchise=franchise,
        organization=organization,
        status=FranchiseUpdateRequest.STATUS_SUBMITTED,
    ).order_by("-submitted_at").first()

    if submitted_request:
        form = FranchiseUpdateRequestForm(instance=submitted_request, disabled=True)
        context = {
            "site_name": "SaaS Home",
            "page_title": "Edit franchise profile",
            "active_page": "vendor",
            "franchise": franchise,
            "update_request": submitted_request,
            "form": form,
            "read_only": True,
        }
        return render(request, "vendor/franchises/edit.html", context)

    update_request = (
        FranchiseUpdateRequest.objects.filter(
            franchise=franchise,
            organization=organization,
            status__in=(FranchiseUpdateRequest.STATUS_DRAFT, FranchiseUpdateRequest.STATUS_REJECTED),
        )
        .order_by("-updated_at")
        .first()
    )
    if not update_request:
        update_request = create_update_request_from_franchise(franchise, request.user)

    form = FranchiseUpdateRequestForm(request.POST or None, instance=update_request)
    if request.method == "POST" and form.is_valid():
        update_request = form.save(commit=False)
        update_request.submitted_by = request.user
        action = request.POST.get("action", "save")
        if action == "submit":
            update_request.save()
            update_request.submit()
            messages.success(request, "Zmiany zostały wysłane do weryfikacji.")
            return redirect("vendor:franchise_edit", slug=franchise.slug)

        update_request.status = FranchiseUpdateRequest.STATUS_DRAFT
        update_request.save()
        messages.success(request, "Szkic zmian został zapisany.")
        return redirect("vendor:franchise_edit", slug=franchise.slug)

    context = {
        "site_name": "SaaS Home",
        "page_title": "Edit franchise profile",
        "active_page": "vendor",
        "franchise": franchise,
        "update_request": update_request,
        "form": form,
        "read_only": False,
    }
    return render(request, "vendor/franchises/edit.html", context)


@login_required
def vendor_franchise_update_submit_view(request, pk):
    update_request = get_object_or_404(
        FranchiseUpdateRequest.objects.select_related("franchise", "organization"),
        pk=pk,
    )
    manageable_franchises = get_user_franchises(request.user)
    if not manageable_franchises.filter(pk=update_request.franchise_id).exists():
        return redirect("vendor:franchises")
    if request.method == "POST" and update_request.status in (
        FranchiseUpdateRequest.STATUS_DRAFT,
        FranchiseUpdateRequest.STATUS_REJECTED,
    ):
        update_request.submitted_by = request.user
        update_request.save(update_fields=["submitted_by", "updated_at"])
        update_request.submit()
        messages.success(request, "Zmiany zostały wysłane do weryfikacji.")
    return redirect("vendor:franchise_edit", slug=update_request.franchise.slug)
