from django.db import migrations


def mark_existing_websites(apps, schema_editor):
    Franchise = apps.get_model("franchises", "Franchise")
    Franchise.objects.exclude(website_url="").filter(
        website_url_status="missing"
    ).update(website_url_status="unverified_seed")


class Migration(migrations.Migration):
    dependencies = [("franchises", "0019_research_launch_completion_status")]

    operations = [migrations.RunPython(mark_existing_websites, migrations.RunPython.noop)]
