from django.contrib import admin

from .models import Franchise, FranchiseCategory, FranchiseLocation, FranchiseUpdateRequest


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
        "organization",
        "category",
        "business_type",
        "min_investment",
        "max_investment",
        "unit_growth_percent_1y",
        "rank_score",
        "is_verified",
        "is_promoted",
        "is_active",
    )
    list_filter = (
        "category",
        "organization",
        "business_type",
        "home_based",
        "part_time_possible",
        "training_provided",
        "financing_available",
        "financial_performance_disclosed",
        "data_status",
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
        (None, {"fields": ("name", "slug", "organization", "category", "short_description", "description", "logo", "website_url")}),
        ("Investment", {"fields": ("min_investment", "max_investment", "initial_fee", "liquid_capital_required", "net_worth_required", "estimated_payback_months", "royalty_fee_text", "marketing_fee_text")}),
        ("Business model", {"fields": ("business_type", "required_premises", "home_based", "part_time_possible", "training_provided", "financing_available")}),
        ("Scale", {"fields": ("founded_year", "franchising_since", "total_units", "poland_units", "franchised_units", "company_owned_units", "units_opened_last_year", "units_closed_last_year", "units_transferred_last_year", "unit_growth_percent_1y")}),
        ("Unit economics and disclosure", {"fields": ("mature_unit_revenue_annual", "mature_unit_operating_profit_annual", "mature_unit_count", "typical_unit_size_min_sqm", "typical_unit_size_max_sqm", "typical_staff_count", "territory_type", "franchise_term_years", "renewal_term_years", "financial_performance_disclosed", "financial_performance_note", "financial_data_as_of", "data_status", "data_source_url")}),
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


@admin.register(FranchiseUpdateRequest)
class FranchiseUpdateRequestAdmin(admin.ModelAdmin):
    list_display = (
        "franchise",
        "organization",
        "submitted_by",
        "status",
        "submitted_at",
        "reviewed_by",
        "reviewed_at",
        "updated_at",
    )
    list_filter = ("status", "organization", "submitted_at", "reviewed_at")
    search_fields = (
        "franchise__name",
        "organization__name",
        "submitted_by__email",
        "submitted_by__username",
    )
    readonly_fields = (
        "franchise",
        "organization",
        "submitted_by",
        "submitted_at",
        "reviewed_by",
        "reviewed_at",
        "created_at",
        "updated_at",
    )
    actions = ("approve_selected_requests", "reject_selected_requests")
    fieldsets = (
        ("Request", {"fields": ("franchise", "organization", "submitted_by", "status")}),
        (
            "Editable profile data",
            {
                "fields": (
                    "short_description",
                    "description",
                    "website_url",
                    "min_investment",
                    "max_investment",
                    "initial_fee",
                    "royalty_fee_text",
                    "marketing_fee_text",
                    "business_type",
                    "required_premises",
                    "home_based",
                    "part_time_possible",
                    "training_provided",
                    "financing_available",
                    "founded_year",
                    "franchising_since",
                    "total_units",
                    "poland_units",
                    "franchised_units",
                    "company_owned_units",
                    "units_opened_last_year",
                    "units_closed_last_year",
                    "units_transferred_last_year",
                    "unit_growth_percent_1y",
                    "liquid_capital_required",
                    "net_worth_required",
                    "franchise_term_years",
                    "renewal_term_years",
                    "estimated_payback_months",
                    "mature_unit_revenue_annual",
                    "mature_unit_operating_profit_annual",
                    "mature_unit_count",
                    "typical_unit_size_min_sqm",
                    "typical_unit_size_max_sqm",
                    "typical_staff_count",
                    "territory_type",
                    "financial_performance_disclosed",
                    "financial_performance_note",
                    "financial_data_as_of",
                    "data_status",
                    "data_source_url",
                )
            },
        ),
        ("Review", {"fields": ("admin_feedback", "reviewed_by", "reviewed_at")}),
        ("Timestamps", {"fields": ("submitted_at", "created_at", "updated_at")}),
    )

    @admin.action(description="Approve selected submitted update requests")
    def approve_selected_requests(self, request, queryset):
        approved_count = 0
        for update_request in queryset.filter(status=FranchiseUpdateRequest.STATUS_SUBMITTED):
            update_request.approve(reviewed_by=request.user)
            approved_count += 1
        self.message_user(request, f"Approved {approved_count} update request(s).")

    @admin.action(description="Reject selected submitted update requests")
    def reject_selected_requests(self, request, queryset):
        rejected_count = 0
        for update_request in queryset.filter(status=FranchiseUpdateRequest.STATUS_SUBMITTED):
            update_request.reject(reviewed_by=request.user, feedback="Rejected by admin.")
            rejected_count += 1
        self.message_user(request, f"Rejected {rejected_count} update request(s).")
