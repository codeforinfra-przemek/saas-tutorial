from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("billing", "0007_organizationsubscription_billing_interval")]

    operations = [
        migrations.AlterField(
            model_name="franchisepromotion",
            name="promotion_type",
            field=models.CharField(
                choices=[
                    ("featured", "Featured"),
                    ("search_boost", "Search boost"),
                    ("verified_badge", "Legacy sponsored visibility"),
                    ("category_top", "Category top"),
                    ("homepage_featured", "Homepage featured"),
                ],
                max_length=40,
            ),
        ),
    ]
