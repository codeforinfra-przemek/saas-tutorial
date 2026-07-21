from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('franchises', '0014_franchiseresearchlaunch_campaign_position_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='franchiseresearchcampaign',
            constraint=models.CheckConstraint(condition=models.Q(('max_concurrent_runs__gte', 1), ('max_concurrent_runs__lte', 5)), name='research_campaign_concurrency_between_1_and_5'),
        ),
        migrations.AddConstraint(
            model_name='franchiseresearchcampaign',
            constraint=models.CheckConstraint(condition=models.Q(('reserved_cost_usd__gte', 0), ('max_total_cost_usd__gte', models.F('reserved_cost_usd'))), name='research_campaign_budget_covers_reservation'),
        ),
    ]
