from django.db import migrations, models


def recalibrate_l1_launches(apps, schema_editor):
    Launch = apps.get_model("franchises", "FranchiseResearchLaunch")
    ReviewField = apps.get_model("franchises", "FranchiseResearchReviewField")
    for launch in Launch.objects.filter(
        profile_id__in=["PL:L1", "PL:L1:v2"],
        result_workspace__isnull=False,
    ).iterator():
        proposed_fields = (
            ReviewField.objects.filter(workspace_id=launch.result_workspace_id)
            .exclude(proposed_values=[])
            .values("target_field")
            .distinct()
            .count()
        )
        workspace = launch.result_workspace
        if proposed_fields <= 2:
            status = "insufficient"
            completion = "insufficient"
            stage = "Niewystarczający L1 — Workbench wymaga uzupełnienia"
        elif not workspace.scope_complete or proposed_fields < 8:
            status = "partial"
            completion = "partial"
            stage = "Częściowy L1 — Workbench gotowy do Human Review"
        else:
            status = "complete"
            completion = "complete"
            stage = "Pełny L1 — Workbench gotowy do Human Review"
        summary = dict(launch.result_summary or {})
        summary.update(
            {
                "completion": completion,
                "proposed_fields": proposed_fields,
                "minimum_proposed_fields": 8,
            }
        )
        Launch.objects.filter(pk=launch.pk).update(
            status=status,
            current_stage=stage,
            result_summary=summary,
        )


class Migration(migrations.Migration):
    dependencies = [("franchises", "0021_validate_published_website_seeds")]

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
                    ("insufficient", "Niewystarczający — wymaga uzupełnienia"),
                    ("failed", "Błąd"),
                    ("cancelled", "Anulowane"),
                ],
                default="queued",
                max_length=20,
            ),
        ),
        migrations.RunPython(recalibrate_l1_launches, migrations.RunPython.noop),
    ]
