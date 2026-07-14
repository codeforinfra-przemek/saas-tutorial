import django.db.models.deletion
from django.db import migrations, models


def copy_franchise_ids(apps, schema_editor):
    Visit = apps.get_model("visits", "Visit")
    Franchise = apps.get_model("franchises", "Franchise")
    franchise_ids = set(Franchise.objects.values_list("id", flat=True))
    for visit in Visit.objects.exclude(franchise_id__isnull=True).iterator():
        if visit.franchise_id in franchise_ids:
            visit.franchise_new_id = visit.franchise_id
            visit.save(update_fields=["franchise_new"])


def normalize_page_types(apps, schema_editor):
    Visit = apps.get_model("visits", "Visit")
    allowed = {"home", "franchise_list", "franchise_detail", "category", "article", "other"}
    for visit in Visit.objects.all().only("id", "page_type").iterator():
        if visit.page_type not in allowed:
            if visit.page_type == "home":
                normalized = "home"
            elif visit.page_type in ("list", "franchise_list_view"):
                normalized = "franchise_list"
            elif visit.page_type in ("detail", "franchise_detail_view"):
                normalized = "franchise_detail"
            else:
                normalized = "other"
            Visit.objects.filter(id=visit.id).update(page_type=normalized)


class Migration(migrations.Migration):
    dependencies = [
        ("franchises", "0002_seed_demo_franchises"),
        ("visits", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="visit",
            old_name="url_path",
            new_name="path",
        ),
        migrations.RenameField(
            model_name="visit",
            old_name="full_url",
            new_name="full_path",
        ),
        migrations.AlterField(
            model_name="visit",
            name="session_key",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.RunPython(normalize_page_types, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="visit",
            name="page_type",
            field=models.CharField(
                choices=[
                    ("home", "Home"),
                    ("franchise_list", "Franchise list"),
                    ("franchise_detail", "Franchise detail"),
                    ("category", "Category"),
                    ("article", "Article"),
                    ("other", "Other"),
                ],
                default="other",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="visit",
            name="franchise_new",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="visits",
                to="franchises.franchise",
            ),
        ),
        migrations.AddField(
            model_name="visit",
            name="utm_source",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="visit",
            name="utm_medium",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="visit",
            name="utm_campaign",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="visit",
            name="utm_content",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="visit",
            name="utm_term",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AlterField(
            model_name="visit",
            name="ip_hash",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.RunPython(copy_franchise_ids, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="visit",
            name="franchise_id",
        ),
        migrations.RenameField(
            model_name="visit",
            old_name="franchise_new",
            new_name="franchise",
        ),
        migrations.CreateModel(
            name="VisitEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("page_view", "Page view"),
                            ("click_cta", "Click CTA"),
                            ("open_lead_form", "Open lead form"),
                            ("submit_lead_form", "Submit lead form"),
                            ("click_website", "Click website"),
                            ("download_pdf", "Download PDF"),
                        ],
                        max_length=40,
                    ),
                ),
                ("value", models.CharField(blank=True, max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "visit",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="visits.visit"),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="visit",
            index=models.Index(fields=["created_at"], name="visits_visi_created_7580ca_idx"),
        ),
        migrations.AddIndex(
            model_name="visit",
            index=models.Index(fields=["page_type", "created_at"], name="visits_visi_page_ty_9f53af_idx"),
        ),
        migrations.AddIndex(
            model_name="visit",
            index=models.Index(fields=["franchise", "created_at"], name="visits_visi_franchi_ac6b13_idx"),
        ),
        migrations.AddIndex(
            model_name="visit",
            index=models.Index(fields=["session_key"], name="visits_visi_session_86d4c8_idx"),
        ),
        migrations.AddIndex(
            model_name="visit",
            index=models.Index(fields=["utm_source"], name="visits_visi_utm_sou_2cf700_idx"),
        ),
        migrations.AddIndex(
            model_name="visitevent",
            index=models.Index(fields=["event_type", "created_at"], name="visits_visi_event_t_e8aa5d_idx"),
        ),
        migrations.AddIndex(
            model_name="visitevent",
            index=models.Index(fields=["visit", "event_type"], name="visits_visi_visit_i_26433f_idx"),
        ),
    ]
