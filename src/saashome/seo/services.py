import re


SITE_NAME = "Porownaj Franczyze"
PRIVATE_PATH_PREFIXES = (
    "/accounts/", "/auth/", "/vendor/", "/internal/", "/leads/", "/visits/",
    "/saved/", "/subscriptions/", "/manage/", "/billing/checkout/",
    "/billing/customer-portal/", "/billing/success/",
)
LISTING_PATH_PREFIXES = ("/franchises/", "/franczyzy/")


def build_absolute_url(request, path):
    if path.startswith(("http://", "https://")):
        return path
    return request.build_absolute_uri(path)


def get_canonical_url(request, obj=None, path=None):
    if obj is not None and callable(getattr(obj, "get_absolute_url", None)):
        path = obj.get_absolute_url()
    return build_absolute_url(request, path or request.path)


def build_meta_title(default_title, site_name=None):
    site_name = site_name or SITE_NAME
    default_title = (default_title or site_name).strip()
    return default_title if default_title == site_name else f"{default_title} | {site_name}"


def truncate_meta_description(text, max_length=155):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1].rsplit(' ', 1)[0]}..."


def should_noindex_request(request):
    return bool(request.GET) and request.path.startswith(LISTING_PATH_PREFIXES)


def get_franchise_seo(franchise, request):
    title = f"{franchise.name} franczyza - koszty, wymagania i informacje"
    description = (
        f"Sprawdz franczyze {franchise.name}: wymagany wklad, oplaty, model wspolpracy "
        "i mozliwosc kontaktu z franczyzodawca."
    )
    return {
        "seo_title": build_meta_title(title),
        "seo_description": truncate_meta_description(description),
        "canonical_url": get_canonical_url(request, obj=franchise),
        "og_type": "website",
    }


def get_category_seo(category, request):
    title = f"Franczyzy {category.name} - porownaj oferty i koszty"
    description = f"Porownaj franczyzy w kategorii {category.name}: wklad, model biznesowy i kluczowe dane ofert."
    return {
        "seo_title": build_meta_title(title),
        "seo_description": truncate_meta_description(description),
        "canonical_url": get_canonical_url(request, obj=category),
        "og_type": "website",
    }


def get_article_seo(article, request):
    return {
        "seo_title": build_meta_title(article.seo_title or article.title),
        "seo_description": truncate_meta_description(article.seo_description or article.excerpt or article.body),
        "canonical_url": article.canonical_url or get_canonical_url(request, obj=article),
        "og_type": "article",
    }


def get_landing_page_seo(landing_page, request):
    return {
        "seo_title": build_meta_title(landing_page.seo_title or landing_page.title),
        "seo_description": truncate_meta_description(
            landing_page.seo_description or landing_page.intro or landing_page.subtitle or landing_page.body
        ),
        "canonical_url": landing_page.canonical_url or get_canonical_url(request, obj=landing_page),
        "og_type": "website",
    }


def seo_context(request):
    robots_meta = ""
    if request.path.startswith(PRIVATE_PATH_PREFIXES):
        robots_meta = "noindex,nofollow"
    elif should_noindex_request(request):
        robots_meta = "noindex,follow"
    return {"default_canonical_url": get_canonical_url(request), "robots_meta": robots_meta}
