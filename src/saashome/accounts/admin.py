from django.contrib import admin

from .models import Organization, OrganizationMembership, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "user_type", "email_verified", "headline", "location", "updated_at")
    list_filter = ("user_type", "email_verified")
    search_fields = ("user__username", "user__email", "headline", "location")


class OrganizationMembershipInline(admin.TabularInline):
    model = OrganizationMembership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "website", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug", "website")
    inlines = (OrganizationMembershipInline,)


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ("organization", "user", "role", "created_at")
    list_filter = ("role",)
    search_fields = ("organization__name", "user__username", "user__email")
    autocomplete_fields = ("organization", "user")
