import json

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from seo.schema import get_article_schema, get_breadcrumb_schema
from seo.services import get_article_seo, get_landing_page_seo

from .models import Article, ArticleCategory, LandingPage
from .services import get_franchises_for_landing_page


def article_list_view(request):
    articles = (
        Article.objects.filter(status=Article.STATUS_PUBLISHED)
        .select_related("category", "author")
        .order_by("-published_at", "-created_at")
    )
    context = {
        "site_name": "SaaS Home",
        "page_title": "Poradnik franczyzowy",
        "active_page": "content",
        "articles": articles,
        "categories": ArticleCategory.objects.filter(is_active=True),
        "featured_articles": articles.filter(is_featured=True)[:3],
        "seo_title": "Poradnik franczyzowy | SaaS Home",
        "seo_description": "Praktyczne poradniki o wyborze, analizie i prowadzeniu franczyzy.",
    }
    return render(request, "content/article_list.html", context)


def article_detail_view(request, slug):
    article = get_object_or_404(
        Article.objects.select_related("category", "author"),
        slug=slug,
        status=Article.STATUS_PUBLISHED,
    )
    related_articles = Article.objects.filter(status=Article.STATUS_PUBLISHED).exclude(id=article.id)
    if article.category_id:
        related_articles = related_articles.filter(category=article.category)

    breadcrumbs = [
        {"name": "Start", "url": request.build_absolute_uri(reverse("home"))},
        {"name": "Poradnik", "url": request.build_absolute_uri(reverse("content:article_list"))},
        {"name": article.title, "url": request.build_absolute_uri(article.get_absolute_url())},
    ]
    context = {
        "site_name": "SaaS Home",
        "page_title": article.title,
        "active_page": "content",
        "article": article,
        "related_articles": related_articles[:3],
        "breadcrumbs": breadcrumbs,
        "json_ld": json.dumps([get_article_schema(article, request), get_breadcrumb_schema(breadcrumbs)], ensure_ascii=False),
    }
    context.update(get_article_seo(article, request))
    return render(request, "content/article_detail.html", context)


def landing_page_detail_view(request, slug):
    landing_page = get_object_or_404(
        LandingPage,
        slug=slug,
        status=LandingPage.STATUS_PUBLISHED,
    )
    franchises = get_franchises_for_landing_page(landing_page)
    breadcrumbs = [
        {"name": "Start", "url": request.build_absolute_uri(reverse("home"))},
        {"name": landing_page.title, "url": request.build_absolute_uri(landing_page.get_absolute_url())},
    ]
    context = {
        "site_name": "SaaS Home",
        "page_title": landing_page.title,
        "active_page": "content",
        "landing_page": landing_page,
        "franchises": franchises,
        "breadcrumbs": breadcrumbs,
        "json_ld": json.dumps([get_breadcrumb_schema(breadcrumbs)], ensure_ascii=False),
    }
    context.update(get_landing_page_seo(landing_page, request))
    return render(request, "content/landing_page_detail.html", context)


def robots_txt_view(request):
    sitemap_url = request.build_absolute_uri("/sitemap.xml")
    content = "\n".join([
        "User-agent: *", "Allow: /", "Disallow: /admin/", "Disallow: /vendor/",
        "Disallow: /internal/", "Disallow: /accounts/", "Disallow: /auth/",
        "Disallow: /saved/", "Disallow: /billing/", f"Sitemap: {sitemap_url}", "",
    ])
    return HttpResponse(content, content_type="text/plain")
