import json

from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from billing.services import apply_promotion_flags
from content.models import Article, LandingPage
from franchises.models import Franchise, FranchiseCategory
from franchises.seo_pages import BUDGET_PAGES, BUSINESS_MODEL_PAGES, get_seo_page
from shortlists.services import get_saved_franchise_ids_for_user

from .schema import get_breadcrumb_schema, get_item_list_schema
from .services import build_meta_title, get_canonical_url, get_category_seo, truncate_meta_description


def _breadcrumbs(request, *items):
    return [{"name": "Start", "url": request.build_absolute_uri(reverse("home"))}, *items]


def _franchise_list_context(request, page, franchises, breadcrumbs, related_articles=None):
    franchise_list = list(franchises)
    return {
        "site_name": "Porownaj Franczyze",
        "page_title": page["title"],
        "active_page": "franchises",
        "franchises": apply_promotion_flags(franchise_list),
        "breadcrumbs": breadcrumbs,
        "seo_title": build_meta_title(page["title"]),
        "seo_description": truncate_meta_description(page["description"]),
        "canonical_url": get_canonical_url(request),
        "og_type": "website",
        "json_ld": json.dumps([get_breadcrumb_schema(breadcrumbs), get_item_list_schema(franchise_list[:20], request)], ensure_ascii=False),
        "related_articles": related_articles or [],
        "categories": FranchiseCategory.objects.filter(is_active=True),
        "budget_pages": BUDGET_PAGES,
        "business_model_pages": BUSINESS_MODEL_PAGES,
        "saved_franchise_ids": get_saved_franchise_ids_for_user(request.user),
    }


def category_detail_view(request, slug):
    category = get_object_or_404(FranchiseCategory, slug=slug, is_active=True)
    franchises = Franchise.objects.filter(is_active=True, category=category).select_related("category")
    related_articles = Article.objects.filter(status=Article.STATUS_PUBLISHED)[:3]
    related_landing_pages = LandingPage.objects.filter(status=LandingPage.STATUS_PUBLISHED, related_category=category)[:3]
    breadcrumbs = _breadcrumbs(
        request,
        {"name": "Franczyzy", "url": request.build_absolute_uri(reverse("franchises:list"))},
        {"name": category.name, "url": request.build_absolute_uri(category.get_absolute_url())},
    )
    context = _franchise_list_context(
        request,
        {"title": f"Franczyzy {category.name}", "description": f"Przegladaj franczyzy {category.name}. Porownaj naklady, model wspolpracy i kluczowe dane ofert."},
        franchises,
        breadcrumbs,
        related_articles,
    )
    context.update(get_category_seo(category, request))
    context.update({"category": category, "related_landing_pages": related_landing_pages, "intro": f"Zestawienie aktywnych ofert w kategorii {category.name}."})
    return render(request, "franchises/category_detail.html", context)


def budget_detail_view(request, slug):
    page = get_seo_page(BUDGET_PAGES, slug)
    if not page:
        raise Http404
    franchises = Franchise.objects.filter(
        is_active=True,
        min_investment__lte=page["max_investment"],
    ).exclude(data_status=Franchise.DATA_STATUS_DEMO).select_related("category")
    breadcrumbs = _breadcrumbs(request, {"name": "Franczyzy", "url": request.build_absolute_uri(reverse("franchises:list"))}, {"name": page["title"], "url": request.build_absolute_uri(request.path)})
    return render(request, "franchises/seo_franchise_list.html", _franchise_list_context(request, page, franchises, breadcrumbs))


def model_detail_view(request, slug):
    page = get_seo_page(BUSINESS_MODEL_PAGES, slug)
    if not page:
        raise Http404
    franchises = Franchise.objects.filter(is_active=True, **page["filters"]).select_related("category")
    breadcrumbs = _breadcrumbs(request, {"name": "Franczyzy", "url": request.build_absolute_uri(reverse("franchises:list"))}, {"name": page["title"], "url": request.build_absolute_uri(request.path)})
    return render(request, "franchises/seo_franchise_list.html", _franchise_list_context(request, page, franchises, breadcrumbs))


def methodology_view(request):
    breadcrumbs = _breadcrumbs(request, {"name": "Metodologia", "url": request.build_absolute_uri(request.path)})
    context = {"site_name": "Porownaj Franczyze", "page_title": "Metodologia rankingu", "breadcrumbs": breadcrumbs, "seo_title": build_meta_title("Metodologia rankingu franczyz"), "seo_description": "Dowiedz sie, jak oznaczamy profile zweryfikowane, zarzadzane i promowane oraz jak aktualizujemy dane.", "canonical_url": get_canonical_url(request), "json_ld": json.dumps([get_breadcrumb_schema(breadcrumbs)], ensure_ascii=False)}
    return render(request, "seo/methodology.html", context)


def how_it_works_view(request):
    breadcrumbs = _breadcrumbs(request, {"name": "Jak to dziala", "url": request.build_absolute_uri(request.path)})
    context = {"site_name": "Porownaj Franczyze", "page_title": "Jak dziala Porownaj Franczyze", "breadcrumbs": breadcrumbs, "seo_title": build_meta_title("Jak dziala Porownaj Franczyze"), "seo_description": "Porownuj profile franczyz, zapisuj interesujace oferty i wysylaj prosby o informacje w jednym miejscu.", "canonical_url": get_canonical_url(request), "json_ld": json.dumps([get_breadcrumb_schema(breadcrumbs)], ensure_ascii=False)}
    return render(request, "seo/how_it_works.html", context)
