from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("visits", "0002_product_analytics"),
    ]

    operations = [
        migrations.AlterField(
            model_name="visitevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("page_view", "Page view"),
                    ("click_cta", "Click CTA"),
                    ("open_lead_form", "Open lead form"),
                    ("submit_lead_form", "Submit lead form"),
                    ("click_website", "Click website"),
                    ("download_pdf", "Download PDF"),
                    ("save_franchise", "Save franchise"),
                    ("unsave_franchise", "Unsave franchise"),
                    ("compare_franchises", "Compare franchises"),
                    ("multi_request_submit", "Multi-request submit"),
                ],
                max_length=40,
            ),
        ),
    ]
