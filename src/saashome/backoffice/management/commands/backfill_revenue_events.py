from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from backoffice.models import RevenueEvent
from backoffice.services.revenue import get_subscription_mrr
from billing.models import OrganizationSubscription


class Command(BaseCommand):
    help = "Create initial RevenueEvent records for subscriptions without revenue history."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        created = 0
        subscriptions = OrganizationSubscription.objects.select_related("organization", "plan").all()
        for subscription in subscriptions:
            if subscription.revenue_events.exists():
                continue
            mrr = get_subscription_mrr(subscription)
            interval = subscription.billing_interval or OrganizationSubscription.INTERVAL_MONTHLY
            amount = subscription.plan.price_yearly if interval == OrganizationSubscription.INTERVAL_YEARLY else subscription.plan.price_monthly
            effective_at = subscription.starts_at or subscription.created_at or timezone.now()
            created += 1
            if dry_run:
                self.stdout.write(f"Would create event for {subscription}")
                continue
            RevenueEvent.objects.create(
                organization=subscription.organization,
                subscription=subscription,
                plan=subscription.plan,
                event_type=RevenueEvent.EVENT_NEW_SUBSCRIPTION,
                billing_interval=interval,
                amount=amount or Decimal("0"),
                currency=subscription.plan.currency,
                mrr_delta=mrr,
                arr_delta=mrr * Decimal("12"),
                effective_at=effective_at,
                notes="Backfilled from existing subscription.",
                metadata={"source": "backfill_revenue_events"},
            )
        label = "Would create" if dry_run else "Created"
        self.stdout.write(self.style.SUCCESS(f"{label} {created} revenue event(s)."))
