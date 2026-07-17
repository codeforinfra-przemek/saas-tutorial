from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from billing.models import Plan
from billing.services import configure_stripe


class Command(BaseCommand):
    help = "Create or reuse Stripe test products and recurring prices for public paid plans."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm-test-mode",
            action="store_true",
            help="Required safety flag. The command refuses live Stripe keys.",
        )

    def handle(self, *args, **options):
        if not options["confirm_test_mode"]:
            raise CommandError("Run again with --confirm-test-mode.")

        stripe_client = configure_stripe()
        if not stripe_client.api_key.startswith("sk_test_"):
            raise CommandError("This command only supports Stripe test-mode keys.")

        products = list(stripe_client.Product.list(active=True, limit=100).auto_paging_iter())
        for plan in Plan.objects.filter(is_active=True, is_public=True).exclude(slug="free"):
            product = next(
                (
                    item
                    for item in products
                    if dict(item.metadata or {}).get("saashome_plan_slug") == plan.slug
                ),
                None,
            )
            if not product:
                product = stripe_client.Product.create(
                    name=f"SaaS Home - {plan.name}",
                    description=plan.description or None,
                    metadata={"saashome_plan_slug": plan.slug},
                )
                products.append(product)

            prices = list(
                stripe_client.Price.list(product=product.id, active=True, limit=100).auto_paging_iter()
            )
            monthly = self._get_or_create_price(stripe_client, plan, product, prices, "month")
            yearly = self._get_or_create_price(stripe_client, plan, product, prices, "year")
            plan.stripe_product_id = product.id
            plan.stripe_price_monthly_id = monthly.id if monthly else ""
            plan.stripe_price_yearly_id = yearly.id if yearly else ""
            plan.save(
                update_fields=[
                    "stripe_product_id",
                    "stripe_price_monthly_id",
                    "stripe_price_yearly_id",
                    "updated_at",
                ]
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"{plan.slug}: product={product.id}, monthly={plan.stripe_price_monthly_id}, yearly={plan.stripe_price_yearly_id}"
                )
            )

    def _get_or_create_price(self, stripe_client, plan, product, prices, interval):
        amount = plan.price_monthly if interval == "month" else plan.price_yearly
        if amount is None or Decimal(amount) <= 0:
            return None
        existing = next(
            (
                item
                for item in prices
                if item.type == "recurring"
                and item.recurring
                and item.recurring.interval == interval
            ),
            None,
        )
        if existing:
            return existing
        price = stripe_client.Price.create(
            product=product.id,
            currency=plan.currency.lower(),
            unit_amount=int(Decimal(amount) * 100),
            recurring={"interval": interval},
            metadata={
                "saashome_plan_slug": plan.slug,
                "billing_interval": "monthly" if interval == "month" else "yearly",
            },
        )
        prices.append(price)
        return price
