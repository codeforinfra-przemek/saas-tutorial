from django.contrib import admin

from .models import Article, ArticleCategory, LandingPage


@admin.register(ArticleCategory)
class ArticleCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "status", "is_featured", "published_at", "updated_at")
    list_filter = ("status", "category", "is_featured", "published_at")
    search_fields = ("title", "excerpt", "body", "seo_title", "seo_description")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "published_at"
    autocomplete_fields = ("author",)
    fieldsets = (
        (None, {"fields": ("title", "slug", "category", "excerpt", "body", "featured_image", "author", "status", "is_featured", "published_at")} ),
        ("SEO (zalecane: tytul do 60 znakow, opis do 155)", {"fields": ("seo_title", "seo_description", "canonical_url")} ),
        ("Daty", {"fields": ("created_at", "updated_at")} ),
    )


@admin.register(LandingPage)
class LandingPageAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "status",
        "related_category",
        "max_investment",
        "business_type",
        "is_featured",
        "published_at",
    )
    list_filter = ("status", "related_category", "business_type", "is_featured", "published_at")
    search_fields = ("title", "subtitle", "intro", "body", "seo_title", "seo_description")
    prepopulated_fields = {"slug": ("title",)}
    filter_horizontal = ("selected_franchises",)
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "published_at"
    fieldsets = (
        (None, {"fields": ("title", "slug", "subtitle", "intro", "body", "status", "is_featured", "published_at")} ),
        ("SEO (zalecane: tytul do 60 znakow, opis do 155)", {"fields": ("seo_title", "seo_description", "canonical_url")} ),
        ("Filtry i oferty", {"fields": ("related_category", "max_investment", "min_investment", "business_type", "home_based", "part_time_possible", "training_provided", "financing_available", "selected_franchises")} ),
        ("CTA", {"fields": ("cta_label", "cta_url")} ),
        ("Daty", {"fields": ("created_at", "updated_at")} ),
    )
