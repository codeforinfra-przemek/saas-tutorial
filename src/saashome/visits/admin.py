from django.contrib import admin

from .models import Visit, VisitEvent


class VisitEventInline(admin.TabularInline):
    model = VisitEvent
    extra = 0
    readonly_fields = ("event_type", "value", "metadata", "created_at")
    can_delete = False


@admin.register(VisitEvent)
class VisitEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event_type", "visit", "value")
    list_filter = ("event_type", "created_at")
    search_fields = ("value", "visit__path", "visit__session_key", "visit__franchise__name")
    readonly_fields = ("visit", "event_type", "value", "metadata", "created_at")


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "page_type",
        "franchise",
        "path",
        "utm_source",
        "session_key",
        "user",
    )
    list_filter = ("page_type", "franchise", "utm_source", "created_at")
    search_fields = (
        "path",
        "full_path",
        "franchise__name",
        "session_key",
        "utm_source",
        "utm_campaign",
    )
    readonly_fields = (
        "user",
        "session_key",
        "path",
        "full_path",
        "created_at",
        "page_type",
        "franchise",
        "referrer",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "user_agent",
        "ip_hash",
    )
    inlines = [VisitEventInline]
