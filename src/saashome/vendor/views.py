from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import OrganizationMembership
from accounts.permissions import can_manage_franchise_profile, staff_required, vendor_required
from accounts.services import get_user_franchises, get_user_organizations
from billing.services import (
    franchise_has_feature,
    get_active_franchise_subscription,
    get_active_franchise_subscription_map,
    get_franchise_plan,
    get_franchise_plan_map,
)
from franchises.forms import FranchiseAssetForm, FranchiseUpdateRequestForm
from franchises.models import FranchiseAsset, FranchiseUpdateRequest
from franchises.services import create_update_request_from_franchise
from leads.forms import LeadStatusForm
from leads.models import Lead
from leads.services import change_lead_status, create_lead_activity, get_vendor_leads_for_user
from leads.models import LeadActivity
from onboarding.models import ClaimProfileRequest
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

    membership_roles = {
        membership.organization_id: membership.get_role_display()
        for membership in OrganizationMembership.objects.filter(
            user=request.user,
            organization__in=organizations,
            is_active=True,
        )
    }
    organization_rows = [
        {
            "organization": organization,
            "role_label": "Administrator" if request.user.is_staff else membership_roles.get(organization.id, "-"),
        }
        for organization in organizations
    ]
    plans_by_franchise = get_franchise_plan_map(franchises)
    subscriptions_by_franchise = get_active_franchise_subscription_map(franchises)
    lead_contact_franchise_ids = [
        franchise.id
        for franchise in franchises
        if getattr(plans_by_franchise.get(franchise.id), "can_view_leads", False)
    ]
    analytics_franchise_ids = [
        franchise.id
        for franchise in franchises
        if getattr(plans_by_franchise.get(franchise.id), "can_view_analytics", False)
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
        can_view_analytics = franchise.id in analytics_franchise_ids
        can_view_leads = franchise.id in lead_contact_franchise_ids
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
                "plan": plans_by_franchise.get(franchise.id),
                "subscription": subscriptions_by_franchise.get(franchise.id),
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
        "can_view_any_lead_contacts": bool(lead_contact_franchise_ids),
        "can_view_any_analytics": bool(analytics_franchise_ids),
        "lead_contact_franchise_ids": lead_contact_franchise_ids,
        "pending_update_requests_count": FranchiseUpdateRequest.objects.filter(
            franchise_id__in=franchise_ids,
            status=FranchiseUpdateRequest.STATUS_SUBMITTED,
        ).count(),
        "pending_claim_requests_count": ClaimProfileRequest.objects.filter(
            user=request.user,
            status__in=(ClaimProfileRequest.STATUS_NEW, ClaimProfileRequest.STATUS_IN_REVIEW),
        ).count(),
    }
    return render(request, "vendor/dashboard.html", context)


@vendor_required
def vendor_franchise_list_view(request):
    franchises = list(get_user_franchises(request.user))
    plans_by_franchise = get_franchise_plan_map(franchises)
    subscriptions_by_franchise = get_active_franchise_subscription_map(franchises)
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
                "plan": plans_by_franchise.get(franchise.id),
                "subscription": subscriptions_by_franchise.get(franchise.id),
            }
        )

    context = {
        "site_name": "SaaS Home",
        "page_title": "My franchises",
        "active_page": "vendor_franchises",
        "rows": rows,
    }
    return render(request, "vendor/franchises/list.html", context)


@vendor_required
def vendor_franchise_edit_view(request, slug):
    franchises = get_user_franchises(request.user)
    franchise = get_object_or_404(franchises, slug=slug)
    organization = franchise.organization
    plan = get_franchise_plan(franchise)
    can_manage = can_manage_franchise_profile(request.user, franchise)

    submitted_request = FranchiseUpdateRequest.objects.filter(
        franchise=franchise,
        organization=organization,
        status=FranchiseUpdateRequest.STATUS_SUBMITTED,
    ).order_by("-submitted_at").first()

    if submitted_request:
        form = FranchiseUpdateRequestForm(instance=submitted_request, disabled=True, plan=plan)
        context = {
            "site_name": "SaaS Home",
            "page_title": "Edit franchise profile",
            "active_page": "vendor_franchises",
            "franchise": franchise,
            "update_request": submitted_request,
            "form": form,
            "read_only": True,
            "plan": plan,
            "can_manage": can_manage,
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
        update_request = create_update_request_from_franchise(franchise, request.user, save=can_manage)

    form = FranchiseUpdateRequestForm(
        request.POST or None,
        instance=update_request,
        plan=plan,
        disabled=not can_manage,
    )
    if request.method == "POST" and not can_manage:
        messages.error(request, "Tylko właściciel lub administrator organizacji może edytować profil.")
        return redirect("vendor:franchise_edit", slug=franchise.slug)
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
        "active_page": "vendor_franchises",
        "franchise": franchise,
        "update_request": update_request,
        "form": form,
        "read_only": False,
        "plan": plan,
        "can_manage": can_manage,
    }
    return render(request, "vendor/franchises/edit.html", context)


@vendor_required
@require_POST
def vendor_franchise_update_submit_view(request, pk):
    update_request = get_object_or_404(
        FranchiseUpdateRequest.objects.select_related("franchise", "organization"),
        pk=pk,
    )
    manageable_franchises = get_user_franchises(request.user)
    if not manageable_franchises.filter(pk=update_request.franchise_id).exists():
        return redirect("vendor:franchises")
    if not can_manage_franchise_profile(request.user, update_request.franchise):
        messages.error(request, "Tylko właściciel lub administrator organizacji może wysłać zmiany.")
        return redirect("vendor:franchise_edit", slug=update_request.franchise.slug)
    if update_request.status in (
        FranchiseUpdateRequest.STATUS_DRAFT,
        FranchiseUpdateRequest.STATUS_REJECTED,
    ):
        update_request.submitted_by = request.user
        update_request.save(update_fields=["submitted_by", "updated_at"])
        update_request.submit()
        messages.success(request, "Zmiany zostały wysłane do weryfikacji.")
    return redirect("vendor:franchise_edit", slug=update_request.franchise.slug)


@vendor_required
def vendor_lead_list_view(request):
    leads = get_vendor_leads_for_user(request.user)
    status = request.GET.get("status", "").strip()
    franchise_id = request.GET.get("franchise", "").strip()
    franchises = get_user_franchises(request.user)
    lead_plan_map = get_franchise_plan_map(franchises)

    if status:
        leads = leads.filter(status=status)
    if franchise_id:
        leads = leads.filter(franchise_id=franchise_id)

    base_leads = get_vendor_leads_for_user(request.user)
    rows = []
    for lead in leads[:200]:
        can_view_contact = getattr(lead_plan_map.get(lead.franchise_id), "can_view_leads", False)
        lead.can_view_contact = can_view_contact
        rows.append({"lead": lead, "can_view_contact": can_view_contact})

    context = {
        "site_name": "SaaS Home",
        "page_title": "Lead Inbox",
        "active_page": "vendor_leads",
        "rows": rows,
        "franchises": franchises,
        "status_choices": LeadStatusForm.VENDOR_STATUS_CHOICES,
        "filters": {"status": status, "franchise": franchise_id},
        "stats": {
            "total": base_leads.count(),
            "new": base_leads.filter(status=Lead.STATUS_NEW).count(),
            "contacted": base_leads.filter(status=Lead.STATUS_CONTACTED).count(),
            "qualified": base_leads.filter(status=Lead.STATUS_QUALIFIED).count(),
        },
    }
    return render(request, "vendor/leads/list.html", context)


@vendor_required
def vendor_lead_detail_view(request, pk):
    lead = get_object_or_404(get_vendor_leads_for_user(request.user), pk=pk)
    can_view_contact = franchise_has_feature(lead.franchise, "can_view_leads")

    if request.method == "GET":
        create_lead_activity(lead, LeadActivity.TYPE_VENDOR_VIEWED, user=request.user)

    form = LeadStatusForm(request.POST or None, current_status=lead.status)
    if request.method == "POST" and form.is_valid():
        change_lead_status(
            lead,
            form.cleaned_data["status"],
            user=request.user,
            note=form.cleaned_data.get("note", ""),
            rejected_reason=form.cleaned_data.get("rejected_reason", ""),
        )
        messages.success(request, "Lead status has been updated.")
        return redirect("vendor:lead_detail", pk=lead.pk)

    context = {
        "site_name": "SaaS Home",
        "page_title": "Lead details",
        "active_page": "vendor_leads",
        "lead": lead,
        "can_view_contact": can_view_contact,
        "form": form,
        "activities": lead.activities.select_related("created_by").order_by("-created_at")[:50],
    }
    return render(request, "vendor/leads/detail.html", context)


@vendor_required
def vendor_franchise_media_view(request, slug):
    franchise = get_object_or_404(get_user_franchises(request.user), slug=slug)
    plan = get_franchise_plan(franchise)
    can_manage = can_manage_franchise_profile(request.user, franchise)
    images = franchise.assets.filter(asset_type=FranchiseAsset.TYPE_IMAGE)
    documents = franchise.assets.filter(asset_type=FranchiseAsset.TYPE_DOCUMENT)
    image_usage = images.exclude(status=FranchiseAsset.STATUS_REJECTED).count()
    document_usage = documents.exclude(status=FranchiseAsset.STATUS_REJECTED).count()

    image_form = FranchiseAssetForm(asset_type=FranchiseAsset.TYPE_IMAGE, prefix="image")
    document_form = FranchiseAssetForm(asset_type=FranchiseAsset.TYPE_DOCUMENT, prefix="document")
    if request.method == "POST":
        if not can_manage:
            return redirect("vendor:franchise_media", slug=slug)
        asset_type = request.POST.get("asset_type")
        if asset_type not in (FranchiseAsset.TYPE_IMAGE, FranchiseAsset.TYPE_DOCUMENT):
            messages.error(request, "Nieprawidłowy typ pliku.")
            return redirect("vendor:franchise_media", slug=slug)
        form = FranchiseAssetForm(
            request.POST,
            request.FILES,
            asset_type=asset_type,
            prefix="image" if asset_type == FranchiseAsset.TYPE_IMAGE else "document",
        )
        current_count = franchise.assets.filter(asset_type=asset_type).exclude(
            status=FranchiseAsset.STATUS_REJECTED,
        ).count()
        limit = (
            getattr(plan, "max_gallery_images", 0)
            if asset_type == FranchiseAsset.TYPE_IMAGE
            else getattr(plan, "max_documents_per_franchise", 0) or 0
        )
        if limit <= current_count:
            messages.error(request, "Limit plików w tym planie został wykorzystany.")
        elif form.is_valid():
            asset = form.save(commit=False)
            asset.franchise = franchise
            asset.asset_type = asset_type
            asset.uploaded_by = request.user
            asset.save()
            messages.success(request, "Plik zapisano i przekazano do moderacji.")
            return redirect("vendor:franchise_media", slug=slug)
        if asset_type == FranchiseAsset.TYPE_IMAGE:
            image_form = form
        else:
            document_form = form

    return render(
        request,
        "vendor/franchises/media.html",
        {
            "site_name": "SaaS Home",
            "page_title": f"Media: {franchise.name}",
            "active_page": "vendor_franchises",
            "franchise": franchise,
            "plan": plan,
            "can_manage": can_manage,
            "images": images,
            "documents": documents,
            "image_form": image_form,
            "document_form": document_form,
            "image_limit": getattr(plan, "max_gallery_images", 0),
            "document_limit": getattr(plan, "max_documents_per_franchise", 0) or 0,
            "image_usage": image_usage,
            "document_usage": document_usage,
        },
    )


@vendor_required
@require_POST
def vendor_franchise_asset_delete_view(request, slug, pk):
    franchise = get_object_or_404(get_user_franchises(request.user), slug=slug)
    if not can_manage_franchise_profile(request.user, franchise):
        return redirect("vendor:franchise_media", slug=slug)
    asset = get_object_or_404(FranchiseAsset, pk=pk, franchise=franchise)
    asset.file.delete(save=False)
    asset.delete()
    messages.success(request, "Plik został usunięty.")
    return redirect("vendor:franchise_media", slug=slug)


@staff_required
@require_POST
def vendor_franchise_asset_review_view(request, slug, pk, decision):
    franchise = get_object_or_404(get_user_franchises(request.user), slug=slug)
    asset = get_object_or_404(FranchiseAsset, pk=pk, franchise=franchise)
    if decision not in (FranchiseAsset.STATUS_APPROVED, FranchiseAsset.STATUS_REJECTED):
        return redirect("vendor:franchise_media", slug=slug)
    asset.status = decision
    asset.reviewed_by = request.user
    asset.reviewed_at = timezone.now()
    asset.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    messages.success(request, "Status pliku został zaktualizowany.")
    return redirect("vendor:franchise_media", slug=slug)
