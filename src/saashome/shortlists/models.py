from django.conf import settings
from django.db import models


class SavedFranchise(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_franchises",
    )
    franchise = models.ForeignKey(
        "franchises.Franchise",
        on_delete=models.CASCADE,
        related_name="saved_by_users",
    )
    session_key = models.CharField(max_length=80, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "franchise"],
                name="unique_saved_franchise_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "created_at"], name="shortlists_user_id_6cdcc3_idx"),
            models.Index(fields=["franchise", "created_at"], name="shortlists_franchi_4ab34f_idx"),
            models.Index(fields=["session_key"], name="shortlists_session_944625_idx"),
        ]

    def __str__(self):
        return f"{self.user} saved {self.franchise}"
