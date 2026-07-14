from django.contrib import admin

from .models import Franchise, FranchiseCategory, FranchiseLocation


@admin.register(FranchiseCategory)
class FranchiseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order", "name")


class FranchiseLocationInline(admin.TabularInline):
    model = FranchiseLocation
    extra = 0
    fields = (
        "location_type",
        "name",
        "city",
        "region",
        "latitude",
        "longitude",
        "is_active",
    )


@admin.register(Franchise)
class FranchiseAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "business_type",
        "min_investment",
        "max_investment",
        "rank_score",
        "is_verified",
        "is_promoted",
        "is_active",
    )
    list_filter = (
        "category",
        "business_type",
        "home_based",
        "part_time_possible",
        "training_provided",
        "financing_available",
        "is_verified",
        "is_promoted",
        "is_featured",
        "is_active",
    )
    search_fields = ("name", "short_description", "description", "website_url")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = [FranchiseLocationInline]
    fieldsets = (
        (None, {"fields": ("name", "slug", "category", "short_description", "description", "logo", "website_url")}),
        ("Investment", {"fields": ("min_investment", "max_investment", "initial_fee", "royalty_fee_text", "marketing_fee_text")}),
        ("Business model", {"fields": ("business_type", "required_premises", "home_based", "part_time_possible", "training_provided", "financing_available")}),
        ("Scale", {"fields": ("founded_year", "franchising_since", "total_units", "poland_units")}),
        ("Ranking", {"fields": ("rank_score", "popularity_score", "editor_rating")}),
        ("Flags", {"fields": ("is_verified", "is_promoted", "is_featured", "is_active")}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(FranchiseLocation)
class FranchiseLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "franchise", "location_type", "city", "region", "latitude", "longitude", "is_active")
    list_filter = ("location_type", "region", "is_active")
    search_fields = ("name", "franchise__name", "city", "region", "address")
    autocomplete_fields = ("franchise",)
