from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.forms import ProfileForm, SignupForm
from accounts.models import Organization, OrganizationMembership
from franchises.models import Franchise, FranchiseCategory
from leads.models import Lead


class AccessControlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="reader",
            email="reader@example.com",
            password="test-password-123",
        )
        cls.vendor = User.objects.create_user(
            username="vendor",
            email="vendor@example.com",
            password="test-password-123",
        )
        cls.suspended_vendor = User.objects.create_user(
            username="suspended",
            email="suspended@example.com",
            password="test-password-123",
        )
        cls.staff = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="test-password-123",
            is_staff=True,
        )

        cls.organization = Organization.objects.create(
            name="Vendor Organization",
            slug="vendor-organization",
            status=Organization.STATUS_ACTIVE,
        )
        cls.other_organization = Organization.objects.create(
            name="Other Organization",
            slug="other-organization",
            status=Organization.STATUS_ACTIVE,
        )
        cls.suspended_organization = Organization.objects.create(
            name="Suspended Organization",
            slug="suspended-organization",
            status=Organization.STATUS_SUSPENDED,
        )
        OrganizationMembership.objects.create(
            user=cls.vendor,
            organization=cls.organization,
            role=OrganizationMembership.ROLE_MEMBER,
            is_active=True,
        )
        OrganizationMembership.objects.create(
            user=cls.suspended_vendor,
            organization=cls.suspended_organization,
            role=OrganizationMembership.ROLE_OWNER,
            is_active=True,
        )

        cls.category = FranchiseCategory.objects.create(
            name="Services",
            slug="services",
        )
        cls.vendor_franchise = Franchise.objects.create(
            name="Vendor Franchise",
            slug="vendor-franchise",
            category=cls.category,
            organization=cls.organization,
            short_description="Vendor-owned profile",
            is_active=True,
        )
        cls.other_franchise = Franchise.objects.create(
            name="Other Franchise",
            slug="other-franchise",
            category=cls.category,
            organization=cls.other_organization,
            short_description="Another vendor's profile",
            is_active=True,
        )
        cls.vendor_lead = Lead.objects.create(
            franchise=cls.vendor_franchise,
            name="Vendor Lead",
            email="lead@example.com",
            phone="123456789",
            privacy_consent=True,
        )
        cls.other_lead = Lead.objects.create(
            franchise=cls.other_franchise,
            name="Other Lead",
            email="other-lead@example.com",
            phone="987654321",
            privacy_consent=True,
        )

    def test_public_pages_are_available_to_anonymous_viewers(self):
        for url in (
            reverse("home"),
            reverse("franchises:list"),
            reverse("franchises:detail", args=[self.vendor_franchise.slug]),
            reverse("content:article_list"),
            reverse("billing:pricing"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_anonymous_viewer_is_redirected_from_private_pages(self):
        for url in (
            reverse("accounts:dashboard"),
            reverse("shortlists:saved_list"),
            reverse("vendor:dashboard"),
            reverse("vendor:franchises"),
            reverse("franchises:manage_list"),
            reverse("leads:list"),
            reverse("visits:list"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)

    def test_regular_user_sees_onboarding_but_not_vendor_or_staff_tools(self):
        self.client.force_login(self.user)

        dashboard = self.client.get(reverse("vendor:dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "not connected to a vendor organization")
        self.assertNotContains(dashboard, 'title="Vendor dashboard"')

        for url in (
            reverse("vendor:franchises"),
            reverse("vendor:leads"),
            reverse("analytics:vendor_analytics"),
            reverse("franchises:manage_list"),
            reverse("leads:list"),
            reverse("visits:list"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_suspended_organization_does_not_grant_vendor_access(self):
        self.client.force_login(self.suspended_vendor)
        self.assertEqual(self.client.get(reverse("vendor:franchises")).status_code, 403)

    def test_vendor_can_only_access_own_franchises_and_leads(self):
        self.client.force_login(self.vendor)

        own_franchise = self.client.get(
            reverse("vendor:franchise_edit", args=[self.vendor_franchise.slug])
        )
        foreign_franchise = self.client.get(
            reverse("vendor:franchise_edit", args=[self.other_franchise.slug])
        )
        own_lead = self.client.get(reverse("vendor:lead_detail", args=[self.vendor_lead.pk]))
        foreign_lead = self.client.get(reverse("vendor:lead_detail", args=[self.other_lead.pk]))

        self.assertEqual(own_franchise.status_code, 200)
        self.assertEqual(foreign_franchise.status_code, 404)
        self.assertEqual(own_lead.status_code, 200)
        self.assertEqual(foreign_lead.status_code, 404)
        self.assertEqual(self.client.get(reverse("franchises:manage_list")).status_code, 403)

    def test_staff_can_access_global_and_vendor_management_views(self):
        self.client.force_login(self.staff)

        for url in (
            reverse("franchises:manage_list"),
            reverse("leads:list"),
            reverse("visits:list"),
            reverse("analytics:admin_analytics"),
            reverse("vendor:franchises"),
            reverse("vendor:leads"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

        vendor_page = self.client.get(reverse("vendor:franchises"))
        self.assertContains(vendor_page, self.vendor_franchise.name)
        self.assertContains(vendor_page, self.other_franchise.name)

    def test_vendor_submit_endpoint_accepts_post_only(self):
        self.client.force_login(self.vendor)
        self.client.get(reverse("vendor:franchise_edit", args=[self.vendor_franchise.slug]))
        update_request = self.vendor_franchise.update_requests.get()
        url = reverse("vendor:franchise_update_submit", args=[update_request.pk])

        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        update_request.refresh_from_db()
        self.assertEqual(update_request.status, update_request.STATUS_SUBMITTED)

    def test_users_cannot_self_assign_vendor_access_in_profile_forms(self):
        self.assertNotIn("user_type", SignupForm().fields)
        self.assertNotIn("user_type", ProfileForm().fields)
