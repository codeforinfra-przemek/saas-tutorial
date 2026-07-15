from django.contrib import admin

from .models import FranchisePromotion, InvestorServiceRequest, OrganizationSubscription, Plan


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "price_monthly", "price_yearly", "currency", "is_active", "sort_order")
    list_filter = ("is_active", "currency")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}


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
