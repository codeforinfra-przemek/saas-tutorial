from django.conf import settings

from .services import build_absolute_url


def get_website_schema(request):
    return {"@context": "https://schema.org", "@type": "WebSite", "name": "Porownaj Franczyze", "url": build_absolute_url(request, "/"), "inLanguage": "pl-PL"}


def get_organization_schema(request):
    return {"@context": "https://schema.org", "@type": "Organization", "name": "Porownaj Franczyze", "url": build_absolute_url(request, "/"), "email": getattr(settings, "DEFAULT_FROM_EMAIL", "")}


def get_breadcrumb_schema(items):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [{"@type": "ListItem", "position": position, "name": item["name"], **({"item": item["url"]} if item.get("url") else {})} for position, item in enumerate(items, start=1)]}


def get_article_schema(article, request):
    schema = {"@context": "https://schema.org", "@type": "Article", "headline": article.title, "description": article.seo_description or article.excerpt, "mainEntityOfPage": build_absolute_url(request, article.get_absolute_url()), "dateModified": article.updated_at.isoformat()}
    if article.published_at:
        schema["datePublished"] = article.published_at.isoformat()
    if article.author_id:
        schema["author"] = {"@type": "Person", "name": article.author.get_username()}
    return schema


def get_franchise_schema(franchise, request):
    schema = {"@context": "https://schema.org", "@type": "Organization", "name": franchise.name, "description": franchise.short_description, "url": build_absolute_url(request, franchise.get_absolute_url())}
    if franchise.website_url:
        schema["sameAs"] = franchise.website_url
    if franchise.logo:
        schema["logo"] = build_absolute_url(request, franchise.logo.url)
    return schema


def get_item_list_schema(items, request):
    return {"@context": "https://schema.org", "@type": "ItemList", "itemListElement": [{"@type": "ListItem", "position": position, "name": item.name, "url": build_absolute_url(request, item.get_absolute_url())} for position, item in enumerate(items, start=1)]}
