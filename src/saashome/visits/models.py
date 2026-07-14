from django.conf import settings
from django.db import models


class Visit(models.Model):
    PAGE_TYPE_HOME = "home"
    PAGE_TYPE_FRANCHISE_LIST = "franchise_list"
    PAGE_TYPE_FRANCHISE_DETAIL = "franchise_detail"
    PAGE_TYPE_CATEGORY = "category"
    PAGE_TYPE_ARTICLE = "article"
    PAGE_TYPE_OTHER = "other"
    PAGE_TYPE_CHOICES = [
        (PAGE_TYPE_HOME, "Home"),
        (PAGE_TYPE_FRANCHISE_LIST, "Franchise list"),
        (PAGE_TYPE_FRANCHISE_DETAIL, "Franchise detail"),
        (PAGE_TYPE_CATEGORY, "Category"),
        (PAGE_TYPE_ARTICLE, "Article"),
        (PAGE_TYPE_OTHER, "Other"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="visits",
    )
    session_key = models.CharField(max_length=80, blank=True)
    path = models.CharField(max_length=2048)
    full_path = models.TextField()
    page_type = models.CharField(max_length=40, choices=PAGE_TYPE_CHOICES, default=PAGE_TYPE_OTHER)
    franchise = models.ForeignKey(
        "franchises.Franchise",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="visits",
    )
    referrer = models.TextField(blank=True)
    utm_source = models.CharField(max_length=120, blank=True)
    utm_medium = models.CharField(max_length=120, blank=True)
    utm_campaign = models.CharField(max_length=160, blank=True)
    utm_content = models.CharField(max_length=160, blank=True)
    utm_term = models.CharField(max_length=160, blank=True)
    user_agent = models.TextField(blank=True)
    ip_hash = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["page_type", "created_at"]),
            models.Index(fields=["franchise", "created_at"]),
            models.Index(fields=["session_key"]),
            models.Index(fields=["utm_source"]),
        ]

    def __str__(self):
        return f"{self.page_type or 'page'} visit at {self.created_at:%Y-%m-%d %H:%M:%S}"


class VisitEvent(models.Model):
    EVENT_PAGE_VIEW = "page_view"
    EVENT_CLICK_CTA = "click_cta"
    EVENT_OPEN_LEAD_FORM = "open_lead_form"
    EVENT_SUBMIT_LEAD_FORM = "submit_lead_form"
    EVENT_CLICK_WEBSITE = "click_website"
    EVENT_DOWNLOAD_PDF = "download_pdf"
    EVENT_TYPE_CHOICES = [
        (EVENT_PAGE_VIEW, "Page view"),
        (EVENT_CLICK_CTA, "Click CTA"),
        (EVENT_OPEN_LEAD_FORM, "Open lead form"),
        (EVENT_SUBMIT_LEAD_FORM, "Submit lead form"),
        (EVENT_CLICK_WEBSITE, "Click website"),
        (EVENT_DOWNLOAD_PDF, "Download PDF"),
    ]

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=40, choices=EVENT_TYPE_CHOICES)
    value = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["visit", "event_type"]),
        ]

    def __str__(self):
        return f"{self.event_type} for visit {self.visit_id}"
