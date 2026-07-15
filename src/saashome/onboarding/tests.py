from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Organization, OrganizationMembership
from franchises.models import Franchise, FranchiseCategory

from .models import ClaimProfileRequest
from .services import approve_claim_request


class ClaimApprovalTests(TestCase):
    def test_approval_assigns_the_selected_organization_and_owner_membership(self):
        user = get_user_model().objects.create_user(
            username="claimant",
            email="claimant@example.com",
            password="test-password-123",
        )
        organization = Organization.objects.create(
            name="Claim Organization",
            slug="claim-organization",
            status=Organization.STATUS_ACTIVE,
        )
        category = FranchiseCategory.objects.create(name="Food", slug="food")
        franchise = Franchise.objects.create(
            name="Claimable Franchise",
            slug="claimable-franchise",
            category=category,
            short_description="Profile waiting for a claim.",
        )
        claim = ClaimProfileRequest.objects.create(
            franchise=franchise,
            user=user,
            claimant_name="Claimant",
            claimant_email=user.email,
            company_name=organization.name,
            privacy_consent=True,
        )

        approved_claim = approve_claim_request(claim, organization=organization)

        approved_claim.refresh_from_db()
        franchise.refresh_from_db()
        membership = OrganizationMembership.objects.get(user=user, organization=organization)
        self.assertEqual(approved_claim.status, ClaimProfileRequest.STATUS_APPROVED)
        self.assertEqual(franchise.organization, organization)
        self.assertTrue(franchise.is_verified)
        self.assertEqual(membership.role, OrganizationMembership.ROLE_OWNER)
        self.assertTrue(membership.is_active)
