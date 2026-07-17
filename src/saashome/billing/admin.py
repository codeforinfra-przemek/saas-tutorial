from django.contrib import admin

from .models import (
    FranchisePromotion,
    FranchiseSubscription,
    FranchiseSubscriptionRequest,
    InvestorServiceRequest,
    OrganizationSubscription,
    Plan,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "price_monthly", "price_yearly", "currency", "is_active", "sort_order")
    list_filter = ("is_active", "currency")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(FranchiseSubscription)
class FranchiseSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "franchise",
        "plan",
        "status",
        "manual_payment_status",
        "starts_at",
        "ends_at",
        "cancel_at_period_end",
    )
    list_filter = ("status", "manual_payment_status", "plan", "cancel_at_period_end")
    search_fields = ("franchise__name", "franchise__organization__name", "admin_notes")
    readonly_fields = ("created_at", "updated_at")


@admin.register(FranchiseSubscriptionRequest)
class FranchiseSubscriptionRequestAdmin(admin.ModelAdmin):
    list_display = ("franchise", "request_type", "requested_plan", "duration_months", "status", "created_at")
    list_filter = ("request_type", "status", "requested_plan", "created_at")
    search_fields = ("franchise__name", "requested_by__email", "vendor_notes", "admin_notes")
    readonly_fields = ("created_at", "updated_at", "reviewed_at")


@admin.register(OrganizationSubscription)
class OrganizationSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("organization", "plan", "status", "manual_payment_status", "starts_at", "ends_at")
    list_filter = ("status", "manual_payment_status", "plan", "starts_at", "ends_at")
    search_fields = ("organization__name", "plan__name", "admin_notes")
    readonly_fields = ("created_at", "updated_at")


@admin.register(FranchisePromotion)
class FranchisePromotionAdmin(admin.ModelAdmin):
    list_display = ("franchise", "promotion_type", "status", "priority", "starts_at", "ends_at")
    list_filter = ("promotion_type", "status", "starts_at", "ends_at")
    search_fields = ("franchise__name", "admin_notes")


@admin.register(InvestorServiceRequest)
class InvestorServiceRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "service_type", "name", "email", "phone", "city", "status")
    list_filter = ("service_type", "status", "created_at")
    search_fields = ("name", "email", "phone", "city", "specialist_area")
    list_editable = ("status",)
    readonly_fields = ("user", "created_at", "updated_at")
