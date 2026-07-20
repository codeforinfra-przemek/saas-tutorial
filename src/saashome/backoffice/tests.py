from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization
from billing.models import OrganizationSubscription, Plan

from .models import RevenueEvent, SalesAccount, SalesActivity, SalesOpportunity
from .services.revenue import get_revenue_overview, get_subscription_mrr
from .services.sales import change_opportunity_stage


class BackofficeTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff = user_model.objects.create_user("backoffice-staff", "staff@example.com", "DemoTest123!", is_staff=True)
        self.user = user_model.objects.create_user("backoffice-user", "user@example.com", "DemoTest123!")
        self.organization = Organization.objects.create(name="Revenue Org", slug="revenue-org")
        self.plan = Plan.objects.create(name="Revenue Plan", slug="revenue-plan", price_monthly=Decimal("120"), price_yearly=Decimal("1200"))
        self.subscription = OrganizationSubscription.objects.create(
            organization=self.organization,
            plan=self.plan,
            status=OrganizationSubscription.STATUS_ACTIVE,
            starts_at=timezone.now() - timedelta(days=1),
            billing_interval=OrganizationSubscription.INTERVAL_MONTHLY,
        )

    def test_staff_only_internal_views_are_not_available_to_regular_users(self):
        self.assertEqual(self.client.get(reverse("backoffice:internal_home")).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("backoffice:revenue_dashboard")).status_code, 302)
        self.client.force_login(self.staff)
        response = self.client.get(reverse("backoffice:revenue_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["robots_meta"], "noindex,nofollow")

    def test_revenue_overview_uses_monthly_mrr_and_events(self):
        RevenueEvent.objects.create(
            organization=self.organization,
            subscription=self.subscription,
            plan=self.plan,
            event_type=RevenueEvent.EVENT_NEW_SUBSCRIPTION,
            billing_interval="monthly",
            amount=Decimal("120"),
            mrr_delta=Decimal("120"),
            arr_delta=Decimal("1440"),
            effective_at=timezone.now(),
        )
        self.assertEqual(get_subscription_mrr(self.subscription), Decimal("120"))
        overview = get_revenue_overview()
        self.assertEqual(overview["mrr"], Decimal("120"))
        self.assertEqual(overview["new_mrr_this_month"], Decimal("120"))

    def test_lost_stage_requires_reason_and_creates_timeline_activity(self):
        account = SalesAccount.objects.create(name="Prospect")
        opportunity = SalesOpportunity.objects.create(account=account, title="Package discussion")
        with self.assertRaises(ValueError):
            change_opportunity_stage(opportunity, SalesOpportunity.STAGE_LOST)
        change_opportunity_stage(opportunity, SalesOpportunity.STAGE_LOST, user=self.staff, lost_reason="Budget")
        opportunity.refresh_from_db()
        self.assertEqual(opportunity.stage, SalesOpportunity.STAGE_LOST)
        self.assertTrue(opportunity.activities.filter(activity_type="status_change").exists())

    def test_staff_can_open_sales_dashboard_and_opportunity_detail(self):
        account = SalesAccount.objects.create(name="Sales prospect")
        opportunity = SalesOpportunity.objects.create(account=account, title="Growth plan", expected_monthly_value=Decimal("300"))
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("backoffice:sales_dashboard")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("backoffice:sales_opportunity_detail", kwargs={"pk": opportunity.pk})).status_code,
            200,
        )

    def test_demo_seed_creates_revenue_and_sales_data_idempotently(self):
        call_command("seed_backoffice_demo")
        initial_counts = {
            "events": RevenueEvent.objects.count(),
            "accounts": SalesAccount.objects.count(),
            "opportunities": SalesOpportunity.objects.count(),
            "activities": SalesActivity.objects.count(),
        }

        self.assertGreaterEqual(initial_counts["events"], 9)
        self.assertGreaterEqual(initial_counts["accounts"], 5)
        self.assertGreaterEqual(initial_counts["opportunities"], 5)
        self.assertGreaterEqual(initial_counts["activities"], 10)
        self.assertTrue(RevenueEvent.objects.filter(event_type=RevenueEvent.EVENT_CHURN).exists())

        call_command("seed_backoffice_demo")
        self.assertEqual(initial_counts["events"], RevenueEvent.objects.count())
        self.assertEqual(initial_counts["accounts"], SalesAccount.objects.count())
        self.assertEqual(initial_counts["opportunities"], SalesOpportunity.objects.count())
        self.assertEqual(initial_counts["activities"], SalesActivity.objects.count())
