from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Count, Q
from django.forms.utils import ErrorList
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from billing.services import apply_promotion_flags, get_organization_plan
from accounts.permissions import staff_required
from leads.forms import LeadForm
from leads.models import Lead
from shortlists.services import get_saved_franchise_ids_for_user, is_franchise_saved_by_user
from visits.models import Visit
from visits.services import create_visit

from .forms import FranchiseLocationForm, FranchiseManagementForm
from .models import Franchise, FranchiseCategory, FranchiseLocation

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
    category_slug = request.GET.get("category", "").strip()
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

    if category_slug:
        franchises = franchises.filter(category__slug=category_slug, category__is_active=True)

    if investment_max:
        try:
            investment_max_value = int(investment_max)
        except ValueError:
            investment_max_value = None
        if investment_max_value is not None:
            franchises = franchises.filter(
                Q(min_investment__lte=investment_max_value)
                | Q(max_investment__lte=investment_max_value)
            )

    franchises = apply_promotion_flags(franchises)

    active_locations = FranchiseLocation.objects.filter(
        franchise__in=franchises,
        is_active=True,
    ).select_related("franchise", "franchise__category")

    context = {
        "site_name": "SaaS Home",
        "page_title": "Franczyzy",
        "active_page": "franchises",
        "franchises": franchises,
        "categories": FranchiseCategory.objects.filter(is_active=True),
        "selected_category": category_slug,
        "q": q,
        "investment_max": investment_max,
        "map_markers": build_map_markers(active_locations),
        "saved_franchise_ids": get_saved_franchise_ids_for_user(request.user),
    }
    return render(request, "franchises/list.html", context)


def franchise_directory_view(request):
    filters = directory_filters_from_request(request)
    franchises = filtered_directory_franchises(filters)
    context = {
        "site_name": "SaaS Home",
        "page_title": "Porównaj franczyzy",
        "active_page": "franchise_directory",
        "franchises": franchises,
        "categories": FranchiseCategory.objects.filter(is_active=True),
        "business_type_choices": Franchise.BUSINESS_TYPE_CHOICES,
        "filters": filters,
        "saved_franchise_ids": get_saved_franchise_ids_for_user(request.user),
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


def franchise_detail_view(request, slug):
    franchise = get_object_or_404(
        Franchise.objects.select_related("category", "organization").prefetch_related("locations"),
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

    context = {
        "site_name": "SaaS Home",
        "page_title": franchise.name,
        "active_page": "franchises",
        "franchise": franchise,
        "locations": locations,
        "lead_form": lead_form,
        "map_markers": build_map_markers(locations),
        "organization_plan": get_organization_plan(franchise.organization),
        "is_saved": is_franchise_saved_by_user(request.user, franchise),
    }
    return render(request, "franchises/detail.html", context)


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
