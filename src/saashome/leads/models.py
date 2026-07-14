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
    contacted_at = models.DateTimeField(null=True, blank=True)
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
