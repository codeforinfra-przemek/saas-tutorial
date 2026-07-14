import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_vendor_organization_fields"),
        ("franchises", "0002_seed_demo_franchises"),
    ]

    operations = [
        migrations.AddField(
            model_name="franchise",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="franchises",
                to="accounts.organization",
            ),
        ),
    ]
