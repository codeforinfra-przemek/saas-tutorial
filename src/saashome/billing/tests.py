from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization, OrganizationMembership
from billing.models import FranchiseSubscription, FranchiseSubscriptionRequest, Plan
from billing.services import approve_subscription_request, franchise_has_feature
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

    def test_owner_can_request_plan_but_member_cannot(self):
        url = reverse(
            "billing:subscription_request",
            kwargs={"slug": self.franchise.slug, "action": "start"},
        )
        self.client.force_login(self.member)
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
