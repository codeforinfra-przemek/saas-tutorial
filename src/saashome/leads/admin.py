from django.contrib import admin

from .models import Lead, LeadActivity


class LeadActivityInline(admin.TabularInline):
    model = LeadActivity
    extra = 0
    can_delete = False
    readonly_fields = (
        "activity_type",
        "created_by",
        "old_status",
        "new_status",
        "note",
        "metadata",
        "created_at",
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "email",
        "phone",
        "city",
        "franchise",
        "visit",
        "investment_budget",
        "status",
        "multi_request_id",
        "last_activity_at",
        "created_at",
    )
    list_filter = (
        "status",
        "franchise",
        "privacy_consent",
        "marketing_consent",
        "utm_source",
        "created_at",
    )
    search_fields = ("name", "email", "phone", "city", "franchise__name")
    list_editable = ("status",)
    readonly_fields = (
        "source_path",
        "visit",
        "referrer",
        "session_key",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "user_agent",
        "ip_hash",
        "multi_request_id",
        "contacted_at",
        "qualified_at",
        "rejected_at",
        "sent_to_vendor_at",
        "last_activity_at",
        "created_at",
        "updated_at",
    )
    inlines = [LeadActivityInline]
    fieldsets = (
        (
            "Lead",
            {
                "fields": (
                    "franchise",
                    "visit",
                    "user",
                    "name",
                    "email",
                    "phone",
                    "city",
                    "investment_budget",
                    "message",
                    "status",
                    "multi_request_id",
                )
            },
        ),
        ("Consents", {"fields": ("privacy_consent", "marketing_consent")}),
        (
            "Workflow",
            {
                "fields": (
                    "vendor_notes",
                    "admin_notes",
                    "contacted_at",
                    "qualified_at",
                    "rejected_at",
                    "rejected_reason",
                    "sent_to_vendor_at",
                    "last_activity_at",
                )
            },
        ),
        (
            "Attribution",
            {
                "fields": (
                    "source_path",
                    "referrer",
                    "session_key",
                    "utm_source",
                    "utm_medium",
                    "utm_campaign",
                    "utm_content",
                    "utm_term",
                    "user_agent",
                    "ip_hash",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(LeadActivity)
class LeadActivityAdmin(admin.ModelAdmin):
    list_display = ("created_at", "lead", "activity_type", "created_by", "old_status", "new_status")
    list_filter = ("activity_type", "created_at")
    search_fields = ("lead__name", "lead__email", "lead__franchise__name", "note")
    readonly_fields = (
        "lead",
        "activity_type",
        "created_by",
        "old_status",
        "new_status",
        "note",
        "metadata",
        "created_at",
    )
