from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('franchises', '0015_franchiseresearchcampaign_research_campaign_concurrency_between_1_and_5_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='franchiseresearchlaunch',
            name='provider_failure_history',
            field=models.JSONField(default=list),
        ),
    ]
