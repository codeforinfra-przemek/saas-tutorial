from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from franchises.models import FranchiseCategory
from franchises.seo_pages import BUDGET_PAGES, BUSINESS_MODEL_PAGES


class FranchiseCategorySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return FranchiseCategory.objects.filter(is_active=True)


class BudgetPageSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.7

    def items(self):
        return BUDGET_PAGES

    def location(self, item):
        return reverse("seo:budget_detail", kwargs={"slug": item["slug"]})


class BusinessModelPageSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.7

    def items(self):
        return BUSINESS_MODEL_PAGES

    def location(self, item):
        return reverse("seo:model_detail", kwargs={"slug": item["slug"]})


class StaticSeoPageSitemap(Sitemap):
    changefreq = "monthly"
    priority = 0.6
    url_names = ("home", "franchises:list", "content:article_list", "billing:pricing", "seo:methodology", "seo:how_it_works")

    def items(self):
        return self.url_names

    def location(self, item):
        return reverse(item)
