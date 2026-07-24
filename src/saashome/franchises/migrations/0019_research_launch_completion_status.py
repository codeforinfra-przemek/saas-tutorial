from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("franchises", "0018_franchise_catalog_status")]

    operations = [
        migrations.AlterField(
            model_name="franchiseresearchlaunch",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "W kolejce"),
                    ("running", "W trakcie"),
                    ("succeeded", "Draft do Human Review (status historyczny)"),
                    ("complete", "Pełny L1 — Draft do Human Review"),
                    ("partial", "Częściowy — Draft do Human Review"),
                    ("failed", "Błąd"),
                    ("cancelled", "Anulowane"),
                ],
                default="queued",
                max_length=20,
            ),
        )
    ]
