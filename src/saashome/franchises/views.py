import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.forms.utils import ErrorList
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from billing.services import apply_promotion_flags, get_franchise_plan
from accounts.permissions import staff_required
from leads.forms import LeadForm
from leads.models import Lead
from shortlists.services import get_saved_franchise_ids_for_user, is_franchise_saved_by_user
from seo.schema import get_breadcrumb_schema, get_franchise_schema
from seo.services import get_canonical_url, get_franchise_seo


FRANCHISE_LIST_PAGE_SIZE = 10
PUBLIC_FRANCHISE_LIST_PAGE_LIMIT = 3
DIRECTORY_PAGE_SIZE = 15
PUBLIC_DIRECTORY_PAGE_LIMIT = 2
from visits.models import Visit
from visits.services import create_visit

from .forms import FranchiseLocationForm, FranchiseManagementForm
from .models import (
    Franchise,
    FranchiseAsset,
    FranchiseCategory,
    FranchiseLocation,
    FranchiseResearchClaimCitation,
    FranchiseResearchEditorialDecision,
    FranchiseResearchField,
    FranchiseResearchReviewField,
    FranchiseResearchTask,
    FranchiseResearchValue,
    FranchiseResearchValueClaim,
)
from .presentation import category_visual, decorate_categories
from .research_fields import L1_PUBLIC_FIELD_ORDER, field_metadata, profile_info

def management_context(**kwargs):
    context = {
        "site_name": "Porównaj Franczyzę",
        "active_page": "franchise_management",
    }
    context.update(kwargs)
    return context


def unique_franchise_slug(instance):
    if instance.slug:
        return instance.slug

    base_slug = slugify(instance.name) or "franczyza"
    slug = base_slug
    counter = 2
    while Franchise.objects.filter(slug=slug).exclude(pk=instance.pk).exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def build_map_markers(locations):
    markers = []
    for location in locations:
        franchise = location.franchise
        markers.append(
            {
                "lat": float(location.latitude),
                "lng": float(location.longitude),
                "franchiseName": franchise.name,
                "franchiseSlug": franchise.slug,
                "city": location.city,
                "category": franchise.category.name,
                "categoryColor": category_visual(franchise.category.slug)["color"],
                "locationName": location.name,
                "locationType": location.location_type,
                "url": franchise.get_absolute_url(),
            }
        )
    return markers


def get_decimal_filter(value):
    if not value:
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def directory_filters_from_request(request):
    return {
        "q": request.GET.get("q", "").strip(),
        "category": request.GET.get("category", "").strip(),
        "investment_max": get_decimal_filter(request.GET.get("investment_max", "").strip()),
        "business_type": request.GET.get("business_type", "").strip(),
        "growth_min": get_decimal_filter(request.GET.get("growth_min", "").strip()),
        "revenue_min": get_decimal_filter(request.GET.get("revenue_min", "").strip()),
        "payback_max": request.GET.get("payback_max", "").strip(),
        "financing": request.GET.get("financing", "").strip(),
        "financials": request.GET.get("financials", "").strip(),
    }


def filtered_directory_franchises(filters):
    franchises = Franchise.objects.filter(is_active=True).select_related("category", "organization")

    if filters["q"]:
        franchises = franchises.filter(
            Q(name__icontains=filters["q"])
            | Q(short_description__icontains=filters["q"])
            | Q(description__icontains=filters["q"])
        )
    if filters["category"]:
        franchises = franchises.filter(category__slug=filters["category"], category__is_active=True)
    uses_measured_data = any(
        (
            filters["investment_max"] is not None,
            filters["growth_min"] is not None,
            filters["revenue_min"] is not None,
            bool(filters["payback_max"]),
            filters["financing"] == "yes",
            filters["financials"] == "yes",
        )
    )
    if uses_measured_data:
        franchises = franchises.exclude(data_status=Franchise.DATA_STATUS_DEMO)
    if filters["investment_max"] is not None:
        franchises = franchises.filter(
            Q(min_investment__lte=filters["investment_max"])
            | Q(max_investment__lte=filters["investment_max"])
        )
    if filters["business_type"]:
        franchises = franchises.filter(business_type=filters["business_type"])
    if filters["growth_min"] is not None:
        franchises = franchises.filter(unit_growth_percent_1y__gte=filters["growth_min"])
    if filters["revenue_min"] is not None:
        franchises = franchises.filter(mature_unit_revenue_annual__gte=filters["revenue_min"])
    if filters["payback_max"]:
        try:
            franchises = franchises.filter(estimated_payback_months__lte=int(filters["payback_max"]))
        except ValueError:
            pass
    if filters["financing"] == "yes":
        franchises = franchises.filter(financing_available=True)
    if filters["financials"] == "yes":
        franchises = franchises.filter(financial_performance_disclosed=True)
    return apply_promotion_flags(franchises)


def franchise_list_view(request):
    q = request.GET.get("q", "").strip()
    selected_categories = list(dict.fromkeys(filter(None, request.GET.getlist("category"))))
    investment_max = request.GET.get("investment_max", "").strip()

    franchises = (
        Franchise.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related("locations")
    )

    if q:
        franchises = franchises.filter(
            Q(name__icontains=q)
            | Q(short_description__icontains=q)
            | Q(description__icontains=q)
        )

    if selected_categories:
        franchises = franchises.filter(category__slug__in=selected_categories, category__is_active=True)

    if investment_max:
        try:
            investment_max_value = int(investment_max)
        except ValueError:
            investment_max_value = None
        if investment_max_value is not None:
            franchises = franchises.exclude(
                data_status=Franchise.DATA_STATUS_DEMO
            ).filter(
                Q(min_investment__lte=investment_max_value)
                | Q(max_investment__lte=investment_max_value)
            )

    franchises = apply_promotion_flags(franchises)
    paginator = Paginator(franchises, FRANCHISE_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    catalog_locked = (
        not request.user.is_authenticated
        and page_obj.number > PUBLIC_FRANCHISE_LIST_PAGE_LIMIT
    )
    visible_franchises = page_obj.object_list if not catalog_locked else Franchise.objects.none()

    active_locations = FranchiseLocation.objects.filter(
        franchise__in=visible_franchises,
        is_active=True,
    ).select_related("franchise", "franchise__category")

    categories = decorate_categories(FranchiseCategory.objects.filter(is_active=True))
    filter_params = request.GET.copy()
    filter_params.pop("category", None)
    filter_params.pop("page", None)
    category_filter_reset_url = reverse("franchises:list")
    if filter_params:
        category_filter_reset_url = f"{category_filter_reset_url}?{filter_params.urlencode()}"

    for category in categories:
        category_params = filter_params.copy()
        updated_categories = [slug for slug in selected_categories if slug != category.slug]
        if category.slug not in selected_categories:
            updated_categories.append(category.slug)
        for slug in updated_categories:
            category_params.appendlist("category", slug)
        category.filter_url = f"{reverse('franchises:list')}?{category_params.urlencode()}"

    page_params = request.GET.copy()
    page_params.pop("page", None)

    context = {
        "site_name": "SaaS Home",
        "page_title": "Franczyzy",
        "active_page": "franchises",
        "franchises": visible_franchises,
        "page_obj": page_obj,
        "total_franchise_count": paginator.count,
        "catalog_locked": catalog_locked,
        "public_page_limit": PUBLIC_FRANCHISE_LIST_PAGE_LIMIT,
        "page_query_string": page_params.urlencode(),
        "categories": categories,
        "selected_categories": selected_categories,
        "category_filter_reset_url": category_filter_reset_url,
        "q": q,
        "investment_max": investment_max,
        "map_markers": build_map_markers(active_locations),
        "saved_franchise_ids": get_saved_franchise_ids_for_user(request.user),
        "canonical_url": get_canonical_url(request),
    }
    return render(request, "franchises/list.html", context)


def franchise_directory_view(request):
    filters = directory_filters_from_request(request)
    franchises = filtered_directory_franchises(filters)
    paginator = Paginator(franchises, DIRECTORY_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    catalog_locked = (
        not request.user.is_authenticated
        and page_obj.number > PUBLIC_DIRECTORY_PAGE_LIMIT
    )
    visible_franchises = page_obj.object_list if not catalog_locked else Franchise.objects.none()
    page_params = request.GET.copy()
    page_params.pop("page", None)

    context = {
        "site_name": "SaaS Home",
        "page_title": "Porównaj franczyzy",
        "active_page": "franchise_directory",
        "franchises": visible_franchises,
        "page_obj": page_obj,
        "total_franchise_count": paginator.count,
        "catalog_locked": catalog_locked,
        "public_page_limit": PUBLIC_DIRECTORY_PAGE_LIMIT,
        "page_query_string": page_params.urlencode(),
        "categories": FranchiseCategory.objects.filter(is_active=True),
        "business_type_choices": Franchise.BUSINESS_TYPE_CHOICES,
        "filters": filters,
        "saved_franchise_ids": get_saved_franchise_ids_for_user(request.user),
        "canonical_url": get_canonical_url(request),
    }
    return render(request, "franchises/directory.html", context)


def franchise_compare_view(request):
    selected_ids = []
    for value in request.GET.get("ids", "").split(","):
        try:
            franchise_id = int(value.strip())
        except (TypeError, ValueError):
            continue
        if franchise_id not in selected_ids:
            selected_ids.append(franchise_id)

    selected_ids = selected_ids[:4]
    franchises_by_id = {
        franchise.id: franchise
        for franchise in Franchise.objects.filter(id__in=selected_ids, is_active=True).select_related("category")
    }
    franchises = [franchises_by_id[franchise_id] for franchise_id in selected_ids if franchise_id in franchises_by_id]

    context = {
        "site_name": "SaaS Home",
        "page_title": "Porównanie franczyz",
        "active_page": "franchise_directory",
        "franchises": franchises,
    }
    return render(request, "franchises/compare.html", context)


def franchise_detail_view(request, slug, data_only=False):
    franchise = get_object_or_404(
        Franchise.objects.select_related("category", "organization").prefetch_related("locations", "assets"),
        slug=slug,
        is_active=True,
    )
    franchise = apply_promotion_flags([franchise])[0]
    if request.method == "GET":
        create_visit(request, page_type=Visit.PAGE_TYPE_FRANCHISE_DETAIL, franchise=franchise)

    locations = [location for location in franchise.locations.all() if location.is_active]
    for location in locations:
        location.franchise = franchise

    lead_form_data = request.session.pop("lead_form_data", None)
    lead_form_errors = request.session.pop("lead_form_errors", None)
    lead_form = LeadForm(lead_form_data or None)
    if lead_form_errors:
        for field_name, errors in lead_form_errors.items():
            error_messages = [error["message"] for error in errors]
            if field_name in lead_form.fields:
                lead_form.errors[field_name] = ErrorList(error_messages)
            else:
                lead_form.add_error(None, " ".join(error_messages))

    plan = get_franchise_plan(franchise)
    latest_research = franchise.research_imports.filter(is_current=True).first()
    latest_finalization = (
        latest_research.workbench_finalizations.order_by("-finalized_at").first()
        if latest_research
        else None
    )
    l1_research_rows = []
    if latest_finalization and latest_research.profile_id in {"PL:L1", "PL:L1:v2"}:
        publications = {
            item.target_field: item
            for item in latest_finalization.published_fields.filter(
                status="projected",
                is_current=True,
            ).select_related("editorial_decision")
        }
        decisions = {
            item.target_field: item
            for item in latest_finalization.field_decisions.all()
        }
        for target_field in L1_PUBLIC_FIELD_ORDER:
            publication = publications.get(target_field)
            decision = decisions.get(target_field)
            evidence = decision.evidence if decision else []
            l1_research_rows.append(
                {
                    "target_field": target_field,
                    "metadata": field_metadata(target_field),
                    "publication": publication,
                    "decision": decision,
                    "source_url": next(
                        (
                            item.get("url")
                            for item in evidence
                            if item.get("url")
                        ),
                        "",
                    ),
                }
            )
    approved_assets = franchise.assets.filter(status=FranchiseAsset.STATUS_APPROVED)
    similar_franchises = apply_promotion_flags(
        Franchise.objects.filter(is_active=True, category=franchise.category)
        .exclude(pk=franchise.pk)
        .select_related("category")[:3]
    )
    breadcrumbs = [
        {"name": "Start", "url": request.build_absolute_uri(reverse("home"))},
        {"name": "Franczyzy", "url": request.build_absolute_uri(reverse("franchises:list"))},
        {"name": franchise.category.name, "url": request.build_absolute_uri(franchise.category.get_absolute_url())},
        {"name": franchise.name, "url": request.build_absolute_uri(franchise.get_absolute_url())},
    ]
    context = {
        "site_name": "SaaS Home",
        "page_title": franchise.name,
        "active_page": "franchises",
        "data_only": data_only,
        "franchise": franchise,
        "locations": locations,
        "lead_form": lead_form,
        "map_markers": build_map_markers(locations),
        "organization_plan": plan,
        "franchise_plan": plan,
        "gallery_images": approved_assets.filter(asset_type=FranchiseAsset.TYPE_IMAGE)[: plan.max_gallery_images]
        if plan and plan.max_gallery_images
        else [],
        "profile_documents": approved_assets.filter(asset_type=FranchiseAsset.TYPE_DOCUMENT)[
            : plan.max_documents_per_franchise
        ]
        if plan and plan.can_show_documents
        else [],
        "show_website": bool(franchise.website_url and (not franchise.organization_id or (plan and plan.can_show_website))),
        "is_saved": is_franchise_saved_by_user(request.user, franchise),
        "similar_franchises": similar_franchises,
        "latest_research": latest_research,
        "latest_finalization": latest_finalization,
        "l1_research_rows": l1_research_rows,
        "published_research_fields": (
            latest_finalization.published_fields.filter(
                status="projected",
                is_current=True,
            ).count()
            if latest_finalization
            else 0
        ),
        "research_profile": (
            profile_info(latest_research.profile_id, latest_research.depth)
            if latest_research
            else None
        ),
        "breadcrumbs": breadcrumbs,
        "json_ld": json.dumps(
            [get_franchise_schema(franchise, request), get_breadcrumb_schema(breadcrumbs)], ensure_ascii=False
        ),
    }
    context.update(get_franchise_seo(franchise, request))
    return render(request, "franchises/detail.html", context)


def franchise_research_detail_view(request, slug):
    franchise = get_object_or_404(
        Franchise.objects.select_related("category"),
        slug=slug,
        is_active=True,
    )
    value_claims = FranchiseResearchValueClaim.objects.select_related(
        "claim"
    ).prefetch_related(
        Prefetch(
            "claim__claim_citations",
            queryset=FranchiseResearchClaimCitation.objects.select_related(
                "citation",
                "citation__source",
            ),
        )
    )
    values = FranchiseResearchValue.objects.prefetch_related(
        Prefetch("value_claims", queryset=value_claims)
    )
    fields = FranchiseResearchField.objects.prefetch_related(
        Prefetch("values", queryset=values)
    )
    tasks = FranchiseResearchTask.objects.prefetch_related(
        Prefetch("fields", queryset=fields)
    )
    research_import = get_object_or_404(
        franchise.research_imports.prefetch_related(
            Prefetch("tasks", queryset=tasks),
            "sources",
        ),
        is_current=True,
    )
    finalization = research_import.workbench_finalizations.order_by(
        "-finalized_at"
    ).first()
    if finalization is None:
        raise Http404("Research nie został jeszcze zatwierdzony do publikacji.")
    editorial_decisions = []
    editorial_unmapped = []
    if finalization is not None:
        editorial_decisions = list(
            FranchiseResearchEditorialDecision.objects.filter(
                finalization=finalization
            ).prefetch_related("supporting_documents")
        )
        editorial_by_key = {
            (item.task_id, item.target_field): item for item in editorial_decisions
        }
        mapped_keys = set()
        for task in research_import.tasks.all():
            for field in task.fields.all():
                key = (task.task_id, field.target_field)
                field.editorial_decision = editorial_by_key.get(key)
                field.presentation_values = list(field.values.all())
                field.presentation_is_unreviewed = False
                field.catalog_metadata = field_metadata(
                    field.target_field,
                    task_title=task.title,
                )
                if field.editorial_decision is not None:
                    mapped_keys.add(key)
                    if (
                        field.editorial_decision.decision
                        == FranchiseResearchReviewField.DECISION_PENDING
                    ):
                        # Pending AI proposals belong only in the staff
                        # Workbench. Public reports expose the field and its
                        # review status, never the proposed value or evidence.
                        field.presentation_is_unreviewed = True
                        field.presentation_values = []
                    elif (
                        field.editorial_decision.value_origin != "ai"
                        or not field.editorial_decision.is_public
                    ):
                        field.presentation_values = []
        editorial_unmapped = [
            item
            for item in editorial_decisions
            if (item.task_id, item.target_field) not in mapped_keys
        ]
        for item in editorial_unmapped:
            item.catalog_metadata = field_metadata(
                item.target_field,
                task_title=item.task_title,
            )
    else:
        for task in research_import.tasks.all():
            for field in task.fields.all():
                field.editorial_decision = None
                field.presentation_values = list(field.values.all())
                field.presentation_is_unreviewed = False
                field.catalog_metadata = field_metadata(
                    field.target_field,
                    task_title=task.title,
                )
    normalization_artifact = research_import.artifacts.filter(
        artifact_type="normalization"
    ).first()
    normalized_payload = normalization_artifact.payload if normalization_artifact else {}
    context = {
        "site_name": "SaaS Home",
        "page_title": f"Pełny raport danych — {franchise.name}",
        "active_page": "franchises",
        "franchise": franchise,
        "research_import": research_import,
        "research_tasks": research_import.tasks.all(),
        "research_sources": research_import.sources.all(),
        "finalization": finalization,
        "research_profile": profile_info(
            research_import.profile_id,
            research_import.depth,
        ),
        "editorial_unmapped": editorial_unmapped,
        "research_warnings": normalized_payload.get("warnings", []),
        "critical_missing_fields": normalized_payload.get(
            "critical_missing_fields", []
        ),
        "unevaluated_critical_fields": normalized_payload.get(
            "unevaluated_critical_fields", []
        ),
        "breadcrumbs": [
            {"name": "Start", "url": request.build_absolute_uri(reverse("home"))},
            {
                "name": "Franczyzy",
                "url": request.build_absolute_uri(reverse("franchises:list")),
            },
            {
                "name": franchise.name,
                "url": request.build_absolute_uri(franchise.get_absolute_url()),
            },
            {
                "name": "Pełny raport danych",
                "url": request.build_absolute_uri(
                    reverse("franchises:research_detail", args=[franchise.slug])
                ),
            },
        ],
    }
    return render(request, "franchises/research_detail.html", context)


@staff_required
def franchise_manage_list_view(request):
    q = request.GET.get("q", "").strip()
    category_slug = request.GET.get("category", "").strip()
    status = request.GET.get("status", "").strip()

    franchises = (
        Franchise.objects.select_related("category", "organization")
        .annotate(lead_count=Count("leads", distinct=True), visit_count=Count("visits", distinct=True))
        .order_by("-is_promoted", "-rank_score", "name")
    )

    if q:
        franchises = franchises.filter(
            Q(name__icontains=q)
            | Q(short_description__icontains=q)
            | Q(description__icontains=q)
            | Q(website_url__icontains=q)
        )
    if category_slug:
        franchises = franchises.filter(category__slug=category_slug)
    if status == "active":
        franchises = franchises.filter(is_active=True)
    elif status == "inactive":
        franchises = franchises.filter(is_active=False)
    elif status == "promoted":
        franchises = franchises.filter(is_promoted=True)
    elif status == "verified":
        franchises = franchises.filter(is_verified=True)

    context = management_context(
        page_title="Zarządzanie franczyzami",
        franchises=franchises[:200],
        categories=FranchiseCategory.objects.order_by("sort_order", "name"),
        filters={
            "q": q,
            "category": category_slug,
            "status": status,
        },
        stats={
            "total": Franchise.objects.count(),
            "active": Franchise.objects.filter(is_active=True).count(),
            "promoted": Franchise.objects.filter(is_promoted=True).count(),
            "verified": Franchise.objects.filter(is_verified=True).count(),
        },
    )
    return render(request, "franchises/manage/list.html", context)


@staff_required
def franchise_manage_detail_view(request, pk):
    franchise = get_object_or_404(
        Franchise.objects.select_related("category", "organization").prefetch_related("locations"),
        pk=pk,
    )
    leads = franchise.leads.select_related("user").order_by("-created_at")[:10]
    locations = franchise.locations.order_by("city", "name")
    context = management_context(
        page_title=f"Zarządzaj: {franchise.name}",
        franchise=franchise,
        locations=locations,
        recent_leads=leads,
        metrics={
            "lead_count": Lead.objects.filter(franchise=franchise).count(),
            "new_leads": Lead.objects.filter(franchise=franchise, status=Lead.STATUS_NEW).count(),
            "visit_count": Visit.objects.filter(franchise=franchise).count(),
            "location_count": locations.count(),
        },
    )
    return render(request, "franchises/manage/detail.html", context)


@staff_required
def franchise_manage_create_view(request):
    form = FranchiseManagementForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        franchise = form.save(commit=False)
        franchise.slug = unique_franchise_slug(franchise)
        franchise.save()
        form.save_m2m()
        messages.success(request, "Franczyza została dodana.")
        return redirect("franchises:manage_detail", pk=franchise.pk)

    context = management_context(
        page_title="Nowa franczyza",
        form=form,
        form_title="Dodaj franczyzę",
        submit_label="Dodaj franczyzę",
    )
    return render(request, "franchises/manage/form.html", context)


@staff_required
def franchise_manage_edit_view(request, pk):
    franchise = get_object_or_404(Franchise, pk=pk)
    form = FranchiseManagementForm(request.POST or None, request.FILES or None, instance=franchise)
    if request.method == "POST" and form.is_valid():
        franchise = form.save(commit=False)
        franchise.slug = unique_franchise_slug(franchise)
        franchise.save()
        form.save_m2m()
        messages.success(request, "Franczyza została zaktualizowana.")
        return redirect("franchises:manage_detail", pk=franchise.pk)

    context = management_context(
        page_title=f"Edytuj: {franchise.name}",
        form=form,
        franchise=franchise,
        form_title="Edytuj franczyzę",
        submit_label="Zapisz zmiany",
    )
    return render(request, "franchises/manage/form.html", context)


@staff_required
def franchise_manage_delete_view(request, pk):
    franchise = get_object_or_404(Franchise, pk=pk)
    if request.method == "POST":
        messages.success(request, f"Franczyza {franchise.name} została usunięta.")
        franchise.delete()
        return redirect("franchises:manage_list")

    context = management_context(
        page_title=f"Usuń: {franchise.name}",
        franchise=franchise,
    )
    return render(request, "franchises/manage/confirm_delete.html", context)


@staff_required
def franchise_location_create_view(request, franchise_pk):
    franchise = get_object_or_404(Franchise, pk=franchise_pk)
    form = FranchiseLocationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        location = form.save(commit=False)
        location.franchise = franchise
        location.save()
        messages.success(request, "Lokalizacja została dodana.")
        return redirect("franchises:manage_detail", pk=franchise.pk)

    context = management_context(
        page_title=f"Nowa lokalizacja: {franchise.name}",
        franchise=franchise,
        form=form,
        form_title="Dodaj lokalizację",
        submit_label="Dodaj lokalizację",
    )
    return render(request, "franchises/manage/location_form.html", context)


@staff_required
def franchise_location_edit_view(request, pk):
    location = get_object_or_404(FranchiseLocation.objects.select_related("franchise"), pk=pk)
    form = FranchiseLocationForm(request.POST or None, instance=location)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Lokalizacja została zaktualizowana.")
        return redirect("franchises:manage_detail", pk=location.franchise_id)

    context = management_context(
        page_title=f"Edytuj lokalizację: {location.name}",
        franchise=location.franchise,
        location=location,
        form=form,
        form_title="Edytuj lokalizację",
        submit_label="Zapisz lokalizację",
    )
    return render(request, "franchises/manage/location_form.html", context)


@staff_required
def franchise_location_delete_view(request, pk):
    location = get_object_or_404(FranchiseLocation.objects.select_related("franchise"), pk=pk)
    franchise_pk = location.franchise_id
    if request.method == "POST":
        messages.success(request, f"Lokalizacja {location.name} została usunięta.")
        location.delete()
        return redirect("franchises:manage_detail", pk=franchise_pk)

    context = management_context(
        page_title=f"Usuń lokalizację: {location.name}",
        franchise=location.franchise,
        location=location,
    )
    return render(request, "franchises/manage/location_confirm_delete.html", context)
