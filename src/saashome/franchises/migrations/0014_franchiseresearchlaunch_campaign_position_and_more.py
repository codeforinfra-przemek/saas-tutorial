import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('franchises', '0013_franchiseresearchpublishedfield'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='franchiseresearchlaunch',
            name='campaign_position',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='FranchiseResearchCampaign',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('campaign_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('name', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True)),
                ('target_country', models.CharField(default='PL', max_length=2)),
                ('profile_id', models.CharField(max_length=80)),
                ('status', models.CharField(choices=[('queued', 'W kolejce'), ('running', 'W trakcie'), ('completed', 'Zakończona'), ('completed_with_errors', 'Zakończona z błędami'), ('cancelled', 'Anulowana')], default='queued', max_length=30)),
                ('configuration', models.JSONField(default=dict)),
                ('max_total_cost_usd', models.DecimalField(decimal_places=2, max_digits=10)),
                ('reserved_cost_usd', models.DecimalField(decimal_places=2, max_digits=10)),
                ('max_concurrent_runs', models.PositiveSmallIntegerField(default=1)),
                ('cancel_requested', models.BooleanField(default=False)),
                ('queued_at', models.DateTimeField(auto_now_add=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='requested_research_campaigns', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-queued_at', '-id'],
            },
        ),
        migrations.AddField(
            model_name='franchiseresearchlaunch',
            name='campaign',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='launches', to='franchises.franchiseresearchcampaign'),
        ),
        migrations.AddConstraint(
            model_name='franchiseresearchlaunch',
            constraint=models.UniqueConstraint(condition=models.Q(('campaign__isnull', False)), fields=('campaign', 'franchise'), name='unique_franchise_per_research_campaign'),
        ),
        migrations.AddIndex(
            model_name='franchiseresearchcampaign',
            index=models.Index(fields=['status', 'queued_at'], name='franchises__status_7827de_idx'),
        ),
    ]
