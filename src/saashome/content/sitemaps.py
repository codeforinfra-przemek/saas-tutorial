from django.contrib.sitemaps import Sitemap

from franchises.models import Franchise

from .models import Article, LandingPage


class ArticleSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        return Article.objects.filter(status=Article.STATUS_PUBLISHED)

    def lastmod(self, obj):
        return obj.updated_at


class LandingPageSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return LandingPage.objects.filter(status=LandingPage.STATUS_PUBLISHED)

    def lastmod(self, obj):
        return obj.updated_at


class FranchiseSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.9

    def items(self):
        return Franchise.objects.filter(is_active=True)

    def lastmod(self, obj):
        return getattr(obj, "updated_at", None)
