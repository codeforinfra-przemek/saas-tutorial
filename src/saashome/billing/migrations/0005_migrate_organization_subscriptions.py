from django.db import migrations
from django.db.models import Q
from django.utils import timezone


PLAN_SLUG_MAP = {
    "premium": "growth",
    "enterprise": "pro",
}


def migrate_subscriptions(apps, schema_editor):
    franchise_model = apps.get_model("franchises", "Franchise")
    franchise_subscription_model = apps.get_model("billing", "FranchiseSubscription")
    organization_subscription_model = apps.get_model("billing", "OrganizationSubscription")
    plan_model = apps.get_model("billing", "Plan")
    now = timezone.now()

    franchises = franchise_model.objects.exclude(organization_id__isnull=True)
    for franchise in franchises.iterator():
        if franchise_subscription_model.objects.filter(franchise_id=franchise.pk).exists():
            continue
        legacy = (
            organization_subscription_model.objects.filter(
                organization_id=franchise.organization_id,
                status__in=("active", "trial"),
                starts_at__lte=now,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
            .select_related("plan")
            .order_by("-starts_at")
            .first()
        )
        if not legacy:
            continue
        target_slug = PLAN_SLUG_MAP.get(legacy.plan.slug, legacy.plan.slug)
        target_plan = plan_model.objects.filter(slug=target_slug).first()
        if not target_plan:
            continue
        franchise_subscription_model.objects.create(
            franchise_id=franchise.pk,
            plan_id=target_plan.pk,
            status="active",
            starts_at=legacy.starts_at,
            ends_at=legacy.ends_at,
            manual_payment_status=legacy.manual_payment_status,
            admin_notes="Migrated from the former organization-level subscription.",
        )


class Migration(migrations.Migration):
    dependencies = [("billing", "0004_seed_franchise_plans")]

    operations = [migrations.RunPython(migrate_subscriptions, migrations.RunPython.noop)]
