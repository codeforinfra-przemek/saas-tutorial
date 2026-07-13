from django.conf import settings
from django.db import models


class Visit(models.Model):
    url_path = models.CharField(max_length=2048)
    full_url = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    page_type = models.CharField(max_length=120, blank=True)
    franchise_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="visits",
    )
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    referrer = models.TextField(blank=True)
    user_agent = models.TextField(blank=True)
    ip_hash = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.page_type or 'page'} visit at {self.created_at:%Y-%m-%d %H:%M:%S}"
