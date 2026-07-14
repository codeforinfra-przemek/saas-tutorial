from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.forms.utils import ErrorList
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from leads.forms import LeadForm
from leads.models import Lead
from visits.models import Visit
from visits.services import create_visit

from .forms import FranchiseLocationForm, FranchiseManagementForm
from .models import Franchise, FranchiseCategory, FranchiseLocation


def staff_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper


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
                "city": location.city,
                "category": franchise.category.name,
                "url": franchise.get_absolute_url(),
            }
        )
    return markers


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
    }
    return render(request, "franchises/list.html", context)


def franchise_detail_view(request, slug):
    franchise = get_object_or_404(
        Franchise.objects.select_related("category").prefetch_related("locations"),
        slug=slug,
        is_active=True,
    )
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
