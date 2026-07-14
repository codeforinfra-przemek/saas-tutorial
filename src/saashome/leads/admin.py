from django.contrib import admin

from .models import Lead


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "email",
        "phone",
        "city",
        "franchise",
        "investment_budget",
        "status",
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
        "referrer",
        "session_key",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "user_agent",
        "ip_hash",
        "contacted_at",
        "sent_to_vendor_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "Lead",
            {
                "fields": (
                    "franchise",
                    "user",
                    "name",
                    "email",
                    "phone",
                    "city",
                    "investment_budget",
                    "message",
                    "status",
                )
            },
        ),
        ("Consents", {"fields": ("privacy_consent", "marketing_consent")}),
        ("Admin", {"fields": ("admin_notes", "contacted_at", "sent_to_vendor_at")}),
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
