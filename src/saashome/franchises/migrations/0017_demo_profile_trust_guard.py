from django.db import migrations, models


def clear_demo_verification(apps, schema_editor):
    Franchise = apps.get_model("franchises", "Franchise")
    Franchise.objects.filter(data_status="demo").update(
        is_verified=False,
        rank_score=0,
        popularity_score=0,
        editor_rating=None,
    )


class Migration(migrations.Migration):
    dependencies = [("franchises", "0016_franchiseresearchlaunch_provider_failure_history")]

    operations = [
        migrations.RunPython(clear_demo_verification, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="franchise",
            constraint=models.CheckConstraint(
                condition=~models.Q(data_status="demo") | models.Q(is_verified=False),
                name="demo_franchise_cannot_be_verified",
            ),
        ),
        migrations.AlterField(
            model_name="franchiseresearchcampaign",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "W kolejce"),
                    ("running", "W trakcie"),
                    ("completed", "Drafty gotowe do Human Review"),
                    ("completed_with_errors", "Drafty częściowo gotowe — są błędy"),
                    ("cancelled", "Anulowana"),
                ],
                default="queued",
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="franchiseresearchlaunch",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "W kolejce"),
                    ("running", "W trakcie"),
                    ("succeeded", "Draft do Human Review"),
                    ("failed", "Błąd"),
                    ("cancelled", "Anulowane"),
                ],
                default="queued",
                max_length=20,
            ),
        ),
    ]
