from django.db import migrations


def validate_published_websites(apps, schema_editor):
    PublishedField = apps.get_model("franchises", "FranchiseResearchPublishedField")
    Franchise = apps.get_model("franchises", "Franchise")
    franchise_ids = PublishedField.objects.filter(
        target_field__in=["websites.official", "websites.franchise_offer"],
        status="projected",
        is_current=True,
    ).values_list("franchise_id", flat=True)
    Franchise.objects.filter(pk__in=franchise_ids).exclude(website_url="").update(
        website_url_status="validated_official"
    )


class Migration(migrations.Migration):
    dependencies = [("franchises", "0020_mark_existing_websites_unverified")]

    operations = [
        migrations.RunPython(validate_published_websites, migrations.RunPython.noop)
    ]
