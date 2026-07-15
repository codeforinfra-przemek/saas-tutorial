from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("franchises", "0004_franchiseupdaterequest"),
    ]

    operations = [
        migrations.CreateModel(
            name="SavedFranchise",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("session_key", models.CharField(blank=True, max_length=80)),
                ("note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "franchise",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="saved_by_users",
                        to="franchises.franchise",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="saved_franchises",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["user", "created_at"], name="shortlists_user_id_6cdcc3_idx"),
                    models.Index(fields=["franchise", "created_at"], name="shortlists_franchi_4ab34f_idx"),
                    models.Index(fields=["session_key"], name="shortlists_session_944625_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("user", "franchise"), name="unique_saved_franchise_per_user"),
                ],
            },
        ),
    ]
