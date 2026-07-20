from django.contrib import admin

from .models import RevenueEvent, SalesAccount, SalesActivity, SalesContact, SalesOpportunity


@admin.register(RevenueEvent)
class RevenueEventAdmin(admin.ModelAdmin):
    list_display = ("effective_at", "organization", "event_type", "plan", "billing_interval", "amount", "mrr_delta", "arr_delta")
    list_filter = ("event_type", "billing_interval", "effective_at")
    search_fields = ("organization__name", "notes")


@admin.register(SalesAccount)
class SalesAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "assigned_to", "organization", "franchise", "next_follow_up_at", "last_activity_at")
    list_filter = ("status", "assigned_to", "source")
    search_fields = ("name", "organization__name", "franchise__name", "notes")


@admin.register(SalesContact)
class SalesContactAdmin(admin.ModelAdmin):
    list_display = ("name", "account", "role", "email", "phone", "is_primary")
    list_filter = ("is_primary",)
    search_fields = ("name", "email", "phone", "account__name")


@admin.register(SalesOpportunity)
class SalesOpportunityAdmin(admin.ModelAdmin):
    list_display = ("title", "account", "stage", "assigned_to", "expected_monthly_value", "probability", "expected_close_date", "next_follow_up_at", "last_activity_at")
    list_filter = ("stage", "assigned_to", "expected_close_date", "next_follow_up_at")
    search_fields = ("title", "account__name", "franchise__name", "organization__name", "notes")


@admin.register(SalesActivity)
class SalesActivityAdmin(admin.ModelAdmin):
    list_display = ("created_at", "account", "opportunity", "activity_type", "subject", "created_by", "due_at", "completed_at")
    list_filter = ("activity_type", "created_at", "due_at", "completed_at", "created_by")
    search_fields = ("account__name", "opportunity__title", "subject", "body")
