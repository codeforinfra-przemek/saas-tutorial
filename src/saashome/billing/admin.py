from django.contrib import admin

from .models import (
    BillingCustomer,
    FranchisePromotion,
    FranchiseSubscription,
    FranchiseSubscriptionRequest,
    InvestorServiceRequest,
    OrganizationSubscription,
    Plan,
    StripeWebhookEvent,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "price_monthly",
        "price_yearly",
        "currency",
        "is_active",
        "is_public",
        "sort_order",
    )
    list_filter = ("is_active", "is_public", "currency")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    fieldsets = (
        ("Plan", {"fields": ("name", "slug", "description", "is_active", "is_public", "sort_order")}),
        ("Cena", {"fields": ("price_monthly", "price_yearly", "currency")}),
        (
            "Stripe",
            {"fields": ("stripe_product_id", "stripe_price_monthly_id", "stripe_price_yearly_id")},
        ),
        (
            "Funkcje",
            {
                "fields": (
                    "can_view_leads",
                    "can_view_analytics",
                    "can_show_website",
                    "can_show_documents",
                    "can_be_verified",
                    "can_be_promoted",
                    "can_receive_priority_leads",
                    "can_feature_in_category",
                    "can_feature_on_homepage",
                    "has_priority_support",
                )
            },
        ),
        (
            "Limity",
            {
                "fields": (
                    "max_franchises",
                    "max_documents_per_franchise",
                    "max_gallery_images",
                    "max_description_length",
                )
            },
        ),
    )


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
        "stripe_status",
        "current_period_end",
    )
    list_filter = ("status", "stripe_status", "manual_payment_status", "plan", "cancel_at_period_end")
    search_fields = (
        "franchise__name",
        "franchise__organization__name",
        "stripe_customer_id",
        "stripe_subscription_id",
        "stripe_price_id",
        "admin_notes",
    )
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


@admin.register(BillingCustomer)
class BillingCustomerAdmin(admin.ModelAdmin):
    list_display = ("organization", "stripe_customer_id", "email", "created_at")
    search_fields = ("organization__name", "stripe_customer_id", "email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(StripeWebhookEvent)
class StripeWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "stripe_event_id", "processed", "processed_at")
    list_filter = ("event_type", "processed", "created_at")
    search_fields = ("stripe_event_id", "event_type", "processing_error")
    readonly_fields = (
        "stripe_event_id",
        "event_type",
        "processed",
        "processing_error",
        "payload",
        "created_at",
        "processed_at",
    )
