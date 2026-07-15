from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("leads", "0003_lead_last_activity_at_lead_qualified_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="lead",
            name="multi_request_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
