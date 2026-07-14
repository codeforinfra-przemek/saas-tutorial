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
