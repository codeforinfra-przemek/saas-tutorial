from django.db.models import Q
from django.shortcuts import get_object_or_404, render

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
    business_type = request.GET.get("business_type", "").strip()

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

    if business_type in dict(Franchise.BUSINESS_TYPE_CHOICES):
        franchises = franchises.filter(business_type=business_type)

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
        "business_type_choices": Franchise.BUSINESS_TYPE_CHOICES,
        "selected_category": category_slug,
        "selected_business_type": business_type,
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
    locations = [location for location in franchise.locations.all() if location.is_active]
    for location in locations:
        location.franchise = franchise

    context = {
        "site_name": "SaaS Home",
        "page_title": franchise.name,
        "active_page": "franchises",
        "franchise": franchise,
        "locations": locations,
        "map_markers": build_map_markers(locations),
    }
    return render(request, "franchises/detail.html", context)
