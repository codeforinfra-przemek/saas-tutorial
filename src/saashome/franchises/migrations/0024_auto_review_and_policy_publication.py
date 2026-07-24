from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("franchises", "0023_research_launch_hybrid_l1_lineage"),
    ]

    operations = [
        migrations.AddField(
            model_name="franchiseresearchworkspace",
            name="auto_reviewed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="franchiseresearchworkspace",
            name="review_policy_version",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="franchiseresearchworkspace",
            name="auto_review_summary",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="franchiseresearchfinalization",
            name="policy_accepted_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="franchiseresearchreviewfield",
            name="decision",
            field=models.CharField(
                choices=[
                    ("pending", "Do sprawdzenia"),
                    ("accepted", "Zaakceptowane"),
                    ("accepted_edited", "Poprawione i zaakceptowane"),
                    ("policy_accepted", "Zaakceptowane przez regułę L1"),
                    ("rejected", "Odrzucone"),
                    ("documented_gap", "Sprawdzono — brak danych"),
                ],
                default="pending",
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="franchiseresearcheditorialdecision",
            name="decision",
            field=models.CharField(
                choices=[
                    ("pending", "Do sprawdzenia"),
                    ("accepted", "Zaakceptowane"),
                    ("accepted_edited", "Poprawione i zaakceptowane"),
                    ("policy_accepted", "Zaakceptowane przez regułę L1"),
                    ("rejected", "Odrzucone"),
                    ("documented_gap", "Sprawdzono — brak danych"),
                ],
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="franchiseresearcheditorialdecision",
            name="value_origin",
            field=models.CharField(
                choices=[
                    ("ai", "AI proposal approved by a human"),
                    ("human", "Human supplied or corrected"),
                    ("none", "No publishable value"),
                    ("policy", "Accepted by a versioned publication policy"),
                ],
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name="franchiseresearchpublishedfield",
            name="value_origin",
            field=models.CharField(
                choices=[
                    ("ai", "AI proposal approved by a human"),
                    ("human", "Human supplied or corrected"),
                    ("none", "No publishable value"),
                    ("policy", "Accepted by a versioned publication policy"),
                ],
                max_length=10,
            ),
        ),
    ]
