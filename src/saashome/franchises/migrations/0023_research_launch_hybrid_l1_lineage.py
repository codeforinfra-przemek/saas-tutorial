from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("franchises", "0022_research_launch_insufficient_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="franchiseresearchlaunch",
            name="seed_sources_reference",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="franchiseresearchlaunch",
            name="seed_extractions_reference",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="franchiseresearchlaunch",
            name="seed_check_reference",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="franchiseresearchlaunch",
            name="resolution_reference",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="franchiseresearchlaunch",
            name="execution_reference",
            field=models.TextField(blank=True),
        ),
    ]
