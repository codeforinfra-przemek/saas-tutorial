from django.conf import settings
from django.db import models
from django.utils import timezone


class ClaimProfileRequest(models.Model):
    STATUS_NEW = "new"
    STATUS_IN_REVIEW = "in_review"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = (
        (STATUS_NEW, "New"),
        (STATUS_IN_REVIEW, "In review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_CANCELLED, "Cancelled"),
    )

    franchise = models.ForeignKey("franchises.Franchise", on_delete=models.CASCADE, related_name="claim_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="franchise_claim_requests")
    organization = models.ForeignKey("accounts.Organization", on_delete=models.SET_NULL, null=True, blank=True, related_name="claim_requests")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW)
    claimant_name = models.CharField(max_length=160)
    claimant_email = models.EmailField()
    claimant_phone = models.CharField(max_length=40, blank=True)
    claimant_role = models.CharField(max_length=120, blank=True)
    company_name = models.CharField(max_length=160)
    company_website = models.URLField(blank=True)
    company_email = models.EmailField(blank=True)
    message = models.TextField(blank=True)
    proof_url = models.URLField(blank=True)
    proof_file = models.FileField(upload_to="onboarding/claim_proofs/", blank=True)
    privacy_consent = models.BooleanField(default=False)
    admin_notes = models.TextField(blank=True)
    admin_feedback = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_franchise_claims")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["franchise", "status"]),
            models.Index(fields=["user", "status"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["claimant_email"]),
            models.Index(fields=["company_email"]),
        ]

    def __str__(self):
        return f"Claim for {self.franchise} by {self.claimant_email}"

    def mark_in_review(self, reviewed_by=None):
        if self.status not in (self.STATUS_NEW, self.STATUS_IN_REVIEW):
            return self
        self.status = self.STATUS_IN_REVIEW
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
        return self

    def reject(self, reviewed_by=None, feedback=""):
        if self.status not in (self.STATUS_NEW, self.STATUS_IN_REVIEW):
            return self
        now = timezone.now()
        self.status = self.STATUS_REJECTED
        self.reviewed_by = reviewed_by
        self.reviewed_at = now
        self.rejected_at = now
        if feedback:
            self.admin_feedback = feedback
        self.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejected_at", "admin_feedback", "updated_at"])
        return self
