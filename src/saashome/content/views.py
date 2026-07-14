from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

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

    context = {
        "site_name": "SaaS Home",
        "page_title": article.title,
        "active_page": "content",
        "article": article,
        "related_articles": related_articles[:3],
        "seo_title": article.meta_title,
        "seo_description": article.meta_description,
        "canonical_url": article.canonical_url,
    }
    return render(request, "content/article_detail.html", context)


def landing_page_detail_view(request, slug):
    landing_page = get_object_or_404(
        LandingPage,
        slug=slug,
        status=LandingPage.STATUS_PUBLISHED,
    )
    franchises = get_franchises_for_landing_page(landing_page)
    context = {
        "site_name": "SaaS Home",
        "page_title": landing_page.title,
        "active_page": "content",
        "landing_page": landing_page,
        "franchises": franchises,
        "seo_title": landing_page.meta_title,
        "seo_description": landing_page.meta_description,
        "canonical_url": landing_page.canonical_url,
    }
    return render(request, "content/landing_page_detail.html", context)


def robots_txt_view(request):
    sitemap_url = request.build_absolute_uri("/sitemap.xml")
    content = f"User-agent: *\nAllow: /\nSitemap: {sitemap_url}\n"
    return HttpResponse(content, content_type="text/plain")
