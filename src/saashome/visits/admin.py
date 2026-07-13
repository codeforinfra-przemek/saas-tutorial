from django.contrib import admin

from .models import Visit


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "page_type",
        "url_path",
        "franchise_id",
        "user",
        "session_key",
    )
    list_filter = ("page_type", "created_at")
    search_fields = ("url_path", "full_url", "referrer", "user_agent", "session_key", "ip_hash")
    readonly_fields = (
        "url_path",
        "full_url",
        "created_at",
        "page_type",
        "franchise_id",
        "user",
        "session_key",
        "referrer",
        "user_agent",
        "ip_hash",
    )
