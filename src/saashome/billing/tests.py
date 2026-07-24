from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization, OrganizationMembership
from billing.models import (
    BillingCustomer,
    FranchiseSubscription,
    FranchiseSubscriptionRequest,
    Plan,
    FranchisePromotion,
    StripeWebhookEvent,
)
from billing.services import (
    apply_promotion_flags,
    approve_subscription_request,
    franchise_has_feature,
    sync_subscription_from_stripe,
)
from franchises.models import Franchise, FranchiseCategory


class FranchiseSubscriptionTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="test-password",
        )
        self.member = user_model.objects.create_user(
            username="member",
            email="member@example.com",
            password="test-password",
        )
        self.admin = user_model.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="test-password",
        )
        self.staff = user_model.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="test-password",
            is_staff=True,
        )
        self.organization = Organization.objects.create(name="Vendor", slug="vendor")
        OrganizationMembership.objects.create(
            user=self.owner,
            organization=self.organization,
            role=OrganizationMembership.ROLE_OWNER,
        )
        OrganizationMembership.objects.create(
            user=self.member,
            organization=self.organization,
            role=OrganizationMembership.ROLE_MEMBER,
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.ROLE_ADMIN,
        )
        category = FranchiseCategory.objects.create(name="Food", slug="food")
        self.franchise = Franchise.objects.create(
            name="Test Franchise",
            slug="test-franchise",
            category=category,
            organization=self.organization,
            short_description="Test profile",
        )
        self.basic, _ = Plan.objects.update_or_create(
            slug="basic",
            defaults={
                "name": "Profil",
                "can_view_leads": True,
                "can_show_documents": True,
                "max_gallery_images": 3,
                "max_documents_per_franchise": 3,
            },
        )
        self.growth, _ = Plan.objects.update_or_create(
            slug="growth",
            defaults={
                "name": "Promocja",
                "can_view_leads": True,
                "can_view_analytics": True,
                "can_be_promoted": True,
                "sort_order": 30,
            },
        )

    def test_only_owner_can_request_plan_changes(self):
        url = reverse(
            "billing:subscription_request",
            kwargs={"slug": self.franchise.slug, "action": "start"},
        )
        self.client.force_login(self.member)
        response = self.client.post(url, {"plan": self.basic.pk, "duration_months": 1})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(FranchiseSubscriptionRequest.objects.exists())

        self.client.force_login(self.admin)
        response = self.client.post(url, {"plan": self.basic.pk, "duration_months": 1})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(FranchiseSubscriptionRequest.objects.exists())

        self.client.force_login(self.owner)
        response = self.client.post(url, {"plan": self.basic.pk, "duration_months": 3})
        self.assertRedirects(
            response,
            reverse("billing:subscription_detail", kwargs={"slug": self.franchise.slug}),
        )
        request = FranchiseSubscriptionRequest.objects.get()
        self.assertEqual(request.requested_plan, self.basic)
        self.assertEqual(request.duration_months, 3)

    def test_vendor_billing_renders_franchise_without_subscription(self):
        self.client.force_login(self.owner)

        response = self.client.get(reverse("billing:vendor_billing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.franchise.name)
        self.assertContains(response, "Free")

    def test_approval_activates_features_and_extension_preserves_period(self):
        request = FranchiseSubscriptionRequest.objects.create(
            franchise=self.franchise,
            request_type=FranchiseSubscriptionRequest.TYPE_START,
            requested_plan=self.growth,
            duration_months=1,
            requested_by=self.owner,
        )
        subscription = approve_subscription_request(request, self.staff)
        self.assertEqual(subscription.status, FranchiseSubscription.STATUS_ACTIVE)
        self.assertTrue(franchise_has_feature(self.franchise, "can_be_promoted"))

        previous_end = subscription.ends_at
        extension = FranchiseSubscriptionRequest.objects.create(
            franchise=self.franchise,
            subscription=subscription,
            request_type=FranchiseSubscriptionRequest.TYPE_EXTEND,
            requested_plan=self.growth,
            duration_months=3,
            requested_by=self.owner,
        )
        subscription = approve_subscription_request(extension, self.staff)
        self.assertGreater(subscription.ends_at, previous_end + timedelta(days=80))

    def test_cancel_keeps_access_until_period_end(self):
        subscription = FranchiseSubscription.objects.create(
            franchise=self.franchise,
            plan=self.basic,
            status=FranchiseSubscription.STATUS_ACTIVE,
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(days=30),
        )
        cancellation = FranchiseSubscriptionRequest.objects.create(
            franchise=self.franchise,
            subscription=subscription,
            request_type=FranchiseSubscriptionRequest.TYPE_CANCEL,
            requested_plan=self.basic,
            requested_by=self.owner,
        )
        approve_subscription_request(cancellation, self.staff)
        subscription.refresh_from_db()
        self.assertTrue(subscription.cancel_at_period_end)
        self.assertTrue(franchise_has_feature(self.franchise, "can_view_leads"))

    def test_manual_subscription_shows_separate_owner_actions(self):
        FranchiseSubscription.objects.create(
            franchise=self.franchise,
            plan=self.basic,
            status=FranchiseSubscription.STATUS_ACTIVE,
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(days=30),
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("billing:subscription_detail", args=[self.franchise.slug]))

        self.assertContains(response, "Przedłuż")
        self.assertContains(response, "Zmień plan")
        self.assertContains(response, "Anuluj odnowienie")
        self.assertContains(response, "Manualna")


class StripeBillingTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="stripe-owner",
            email="stripe-owner@example.com",
            password="test-password",
        )
        self.member = user_model.objects.create_user(
            username="stripe-member",
            email="stripe-member@example.com",
            password="test-password",
        )
        self.admin = user_model.objects.create_user(
            username="stripe-admin",
            email="stripe-admin@example.com",
            password="test-password",
        )
        self.organization = Organization.objects.create(
            name="Stripe Vendor",
            slug="stripe-vendor",
            billing_email=self.owner.email,
        )
        OrganizationMembership.objects.create(
            user=self.owner,
            organization=self.organization,
            role=OrganizationMembership.ROLE_OWNER,
        )
        OrganizationMembership.objects.create(
            user=self.member,
            organization=self.organization,
            role=OrganizationMembership.ROLE_MEMBER,
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role=OrganizationMembership.ROLE_ADMIN,
        )
        category = FranchiseCategory.objects.create(name="Stripe Food", slug="stripe-food")
        self.franchise = Franchise.objects.create(
            name="Stripe Franchise",
            slug="stripe-franchise",
            category=category,
            organization=self.organization,
            short_description="Stripe test profile",
        )
        self.plan, _ = Plan.objects.update_or_create(
            slug="stripe-growth",
            defaults={
                "name": "Stripe Growth",
                "is_active": True,
                "is_public": True,
                "can_view_leads": True,
                "stripe_price_monthly_id": "price_monthly_test",
                "stripe_price_yearly_id": "price_yearly_test",
            },
        )
        self.customer = BillingCustomer.objects.create(
            organization=self.organization,
            stripe_customer_id="cus_test",
            email=self.owner.email,
        )

    @patch("billing.views.create_checkout_session", return_value="https://checkout.stripe.test/session")
    def test_only_owner_can_start_checkout(self, create_checkout):
        url = reverse("billing:checkout", args=[self.plan.slug])
        payload = {"franchise_id": self.franchise.pk, "billing_interval": "monthly"}

        self.client.force_login(self.member)
        self.assertEqual(self.client.post(url, payload).status_code, 403)
        create_checkout.assert_not_called()

        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(url, payload).status_code, 403)
        create_checkout.assert_not_called()

        self.client.force_login(self.owner)
        response = self.client.post(url, payload)
        self.assertRedirects(response, "https://checkout.stripe.test/session", fetch_redirect_response=False)
        create_checkout.assert_called_once()

    def test_subscription_sync_updates_only_metadata_franchise(self):
        now = int(timezone.now().timestamp())
        stripe_subscription = {
            "id": "sub_test",
            "customer": self.customer.stripe_customer_id,
            "status": "active",
            "current_period_start": now,
            "current_period_end": now + 30 * 24 * 60 * 60,
            "cancel_at_period_end": False,
            "metadata": {
                "franchise_id": str(self.franchise.pk),
                "organization_id": str(self.organization.pk),
                "billing_interval": "monthly",
            },
            "items": {"data": [{"price": {"id": self.plan.stripe_price_monthly_id}}]},
        }

        subscription = sync_subscription_from_stripe(stripe_subscription)

        self.assertEqual(subscription.franchise, self.franchise)
        self.assertEqual(subscription.plan, self.plan)
        self.assertEqual(subscription.status, FranchiseSubscription.STATUS_ACTIVE)
        self.assertEqual(subscription.stripe_subscription_id, "sub_test")
        self.assertTrue(franchise_has_feature(self.franchise, "can_view_leads"))

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    @patch("billing.views.process_stripe_event")
    @patch("billing.views.stripe.Webhook.construct_event")
    def test_webhook_is_idempotent(self, construct_event, process_event):
        event = {
            "id": "evt_test",
            "type": "customer.subscription.updated",
            "data": {"object": {}},
        }
        construct_event.return_value = event
        url = reverse("billing:stripe_webhook")

        first = self.client.post(
            url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )
        second = self.client.post(
            url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        process_event.assert_called_once()
        webhook_event = StripeWebhookEvent.objects.get(stripe_event_id="evt_test")
        self.assertTrue(webhook_event.processed)

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    @patch("billing.views.process_stripe_event")
    @patch("billing.views.stripe.Webhook.construct_event")
    def test_failed_webhook_is_saved_and_can_be_retried(self, construct_event, process_event):
        construct_event.return_value = {
            "id": "evt_retry",
            "type": "customer.subscription.updated",
            "data": {"object": {}},
        }
        process_event.side_effect = [RuntimeError("temporary failure"), None]
        url = reverse("billing:stripe_webhook")

        first = self.client.post(
            url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )
        failed_event = StripeWebhookEvent.objects.get(stripe_event_id="evt_retry")
        self.assertEqual(first.status_code, 500)
        self.assertFalse(failed_event.processed)
        self.assertIn("temporary failure", failed_event.processing_error)

        second = self.client.post(
            url,
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test-signature",
        )
        failed_event.refresh_from_db()
        self.assertEqual(second.status_code, 200)
        self.assertTrue(failed_event.processed)
        self.assertEqual(failed_event.processing_error, "")


class PromotionTrustSeparationTests(TestCase):
    def setUp(self):
        category = FranchiseCategory.objects.create(name="Trust", slug="trust")
        self.franchise = Franchise.objects.create(
            name="Sponsored but unverified",
            slug="sponsored-unverified",
            category=category,
            short_description="Trust fixture",
            data_status=Franchise.DATA_STATUS_RESEARCH_WITH_GAPS,
            is_verified=False,
        )

    def test_legacy_paid_verified_product_is_only_a_promotion(self):
        FranchisePromotion.objects.create(
            franchise=self.franchise,
            promotion_type=FranchisePromotion.TYPE_VERIFIED_BADGE,
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1),
            priority=100,
        )

        decorated = apply_promotion_flags([self.franchise])[0]

        self.assertTrue(decorated.display_promoted)
        self.assertTrue(decorated.has_legacy_verified_promotion)
        self.assertFalse(decorated.display_verified)
        self.assertFalse(decorated.display_data_verified)
        self.assertTrue(decorated.display_research_with_gaps)
