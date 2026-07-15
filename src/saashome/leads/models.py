from django.conf import settings
from django.db import models


class Lead(models.Model):
    STATUS_NEW = "new"
    STATUS_CONTACTED = "contacted"
    STATUS_QUALIFIED = "qualified"
    STATUS_SENT_TO_VENDOR = "sent_to_vendor"
    STATUS_REJECTED = "rejected"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_NEW, "New"),
        (STATUS_CONTACTED, "Contacted"),
        (STATUS_QUALIFIED, "Qualified"),
        (STATUS_SENT_TO_VENDOR, "Sent to vendor"),
        (STATUS_REJECTED, "Rejected"),
        (STATUS_CLOSED, "Closed"),
    ]

    franchise = models.ForeignKey(
        "franchises.Franchise",
        on_delete=models.CASCADE,
        related_name="leads",
    )
    visit = models.ForeignKey(
        "visits.Visit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="franchise_leads",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=160)
    email = models.EmailField()
    phone = models.CharField(max_length=40)
    city = models.CharField(max_length=120, blank=True)
    investment_budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_NEW)

    source_path = models.CharField(max_length=500, blank=True)
    referrer = models.CharField(max_length=500, blank=True)
    session_key = models.CharField(max_length=80, blank=True)
    utm_source = models.CharField(max_length=120, blank=True)
    utm_medium = models.CharField(max_length=120, blank=True)
    utm_campaign = models.CharField(max_length=160, blank=True)
    utm_content = models.CharField(max_length=160, blank=True)
    utm_term = models.CharField(max_length=160, blank=True)
    user_agent = models.TextField(blank=True)
    ip_hash = models.CharField(max_length=128, blank=True)

    privacy_consent = models.BooleanField(default=False)
    marketing_consent = models.BooleanField(default=False)
    admin_notes = models.TextField(blank=True)
    vendor_notes = models.TextField(blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True)
    qualified_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_reason = models.CharField(max_length=255, blank=True)
    sent_to_vendor_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["franchise", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["email"]),
            models.Index(fields=["session_key"]),
            models.Index(fields=["utm_source"]),
        ]

    def __str__(self):
        return f"{self.name} - {self.franchise}"


class LeadActivity(models.Model):
    TYPE_LEAD_CREATED = "lead_created"
    TYPE_STATUS_CHANGED = "status_changed"
    TYPE_NOTE_ADDED = "note_added"
    TYPE_EMAIL_NOTIFICATION_SENT = "email_notification_sent"
    TYPE_EMAIL_NOTIFICATION_FAILED = "email_notification_failed"
    TYPE_VENDOR_VIEWED = "vendor_viewed"
    ACTIVITY_TYPE_CHOICES = (
        (TYPE_LEAD_CREATED, "Lead created"),
        (TYPE_STATUS_CHANGED, "Status changed"),
        (TYPE_NOTE_ADDED, "Note added"),
        (TYPE_EMAIL_NOTIFICATION_SENT, "Email notification sent"),
        (TYPE_EMAIL_NOTIFICATION_FAILED, "Email notification failed"),
        (TYPE_VENDOR_VIEWED, "Vendor viewed"),
    )

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="activities")
    activity_type = models.CharField(max_length=40, choices=ACTIVITY_TYPE_CHOICES)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_activities",
    )
    old_status = models.CharField(max_length=30, blank=True)
    new_status = models.CharField(max_length=30, blank=True)
    note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["lead", "created_at"]),
            models.Index(fields=["activity_type", "created_at"]),
            models.Index(fields=["created_by", "created_at"]),
        ]

    def __str__(self):
        return f"{self.activity_type} for {self.lead}"
