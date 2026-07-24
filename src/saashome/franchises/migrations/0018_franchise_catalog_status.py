from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("franchises", "0017_demo_profile_trust_guard")]

    operations = [
        migrations.AddField(
            model_name="franchise",
            name="website_url_status",
            field=models.CharField(
                choices=[
                    ("missing", "Brak"),
                    ("unverified_seed", "Niezweryfikowany seed"),
                    ("validated_official", "Zweryfikowana strona oficjalna"),
                    ("rejected", "Odrzucona"),
                ],
                default="missing",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="franchise",
            name="market_status",
            field=models.CharField(
                choices=[
                    ("listed", "Wpis w aktualnym katalogu — do walidacji"),
                    ("active", "Aktywna — potwierdzone"),
                    ("inactive", "Nieaktywna / zamknięta"),
                    ("uncertain", "Status niepewny"),
                ],
                default="uncertain",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="franchise",
            name="recruitment_status",
            field=models.CharField(
                choices=[
                    ("listed_offer", "Oferta widoczna w katalogu — do walidacji"),
                    ("confirmed_open", "Nabór potwierdzony"),
                    ("not_recruiting", "Brak naboru"),
                    ("unknown", "Nieustalony"),
                ],
                default="unknown",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="franchise",
            name="market_status_checked_at",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="franchise",
            name="catalog_sources",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="franchise",
            name="catalog_imported_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
