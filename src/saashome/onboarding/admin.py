from django.contrib import admin

from .models import ClaimProfileRequest
from .services import approve_claim_request, reject_claim_request


@admin.register(ClaimProfileRequest)
class ClaimProfileRequestAdmin(admin.ModelAdmin):
    list_display = ("franchise", "company_name", "claimant_email", "user", "status", "created_at", "reviewed_by", "reviewed_at")
    list_filter = ("status", "created_at", "reviewed_at")
    search_fields = ("franchise__name", "company_name", "claimant_email", "company_email", "user__email", "user__username")
    readonly_fields = ("status", "created_at", "updated_at", "reviewed_at", "approved_at", "rejected_at")
    actions = ("mark_selected_in_review", "approve_selected_claims", "reject_selected_claims")
    fieldsets = (
        ("Claim", {"fields": ("franchise", "user", "organization", "status")} ),
        ("Claimant", {"fields": ("claimant_name", "claimant_email", "claimant_phone", "claimant_role")} ),
        ("Company", {"fields": ("company_name", "company_website", "company_email")} ),
        ("Proof", {"fields": ("message", "proof_url", "proof_file", "privacy_consent")} ),
        ("Review", {"fields": ("admin_notes", "admin_feedback", "reviewed_by", "reviewed_at", "approved_at", "rejected_at")} ),
        ("Timestamps", {"fields": ("created_at", "updated_at")} ),
    )

    @admin.action(description="Mark selected claims as in review")
    def mark_selected_in_review(self, request, queryset):
        count = 0
        for claim in queryset.filter(status__in=(ClaimProfileRequest.STATUS_NEW, ClaimProfileRequest.STATUS_IN_REVIEW)):
            claim.mark_in_review(reviewed_by=request.user)
            count += 1
        self.message_user(request, f"Marked {count} claim(s) as in review.")

    @admin.action(description="Approve selected claims")
    def approve_selected_claims(self, request, queryset):
        count = 0
        for claim in queryset.filter(status__in=(ClaimProfileRequest.STATUS_NEW, ClaimProfileRequest.STATUS_IN_REVIEW)):
            approve_claim_request(claim, reviewed_by=request.user)
            count += 1
        self.message_user(request, f"Approved {count} claim(s).")

    @admin.action(description="Reject selected claims")
    def reject_selected_claims(self, request, queryset):
        count = 0
        for claim in queryset.filter(status__in=(ClaimProfileRequest.STATUS_NEW, ClaimProfileRequest.STATUS_IN_REVIEW)):
            reject_claim_request(
                claim,
                reviewed_by=request.user,
                feedback=claim.admin_feedback or "Rejected by admin.",
            )
            count += 1
        self.message_user(request, f"Rejected {count} claim(s).")
