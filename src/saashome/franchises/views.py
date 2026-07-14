from django.db.models import Q
from django.forms.utils import ErrorList
from django.shortcuts import get_object_or_404, render

from leads.forms import LeadForm
from visits.models import Visit
from visits.services import create_visit

from .models import Franchise, FranchiseCategory, FranchiseLocation


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
