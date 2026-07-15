from django.contrib import admin

from .models import SavedFranchise


@admin.register(SavedFranchise)
class SavedFranchiseAdmin(admin.ModelAdmin):
    list_display = ("user", "franchise", "created_at")
    list_filter = ("created_at", "franchise__category")
    search_fields = ("user__email", "user__username", "franchise__name")
    readonly_fields = ("created_at", "updated_at")
