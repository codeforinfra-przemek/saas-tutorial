from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization
from billing.models import OrganizationSubscription, Plan
from franchises.models import (
    Franchise,
    FranchiseCategory,
    FranchiseResearchDocument,
    FranchiseResearchReviewField,
    FranchiseResearchWorkspace,
)

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

        self.client.force_login(self.staff)
        for url_name in ("backoffice:internal_home", "backoffice:revenue_dashboard", "backoffice:sales_dashboard"):
            self.assertEqual(self.client.get(reverse(url_name)).status_code, 200)

        call_command("seed_backoffice_demo")
        self.assertEqual(initial_counts["events"], RevenueEvent.objects.count())
        self.assertEqual(initial_counts["accounts"], SalesAccount.objects.count())
        self.assertEqual(initial_counts["opportunities"], SalesOpportunity.objects.count())
        self.assertEqual(initial_counts["activities"], SalesActivity.objects.count())


class ResearchWorkbenchViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.staff = user_model.objects.create_user(
            "research-staff",
            "research@example.com",
            "DemoTest123!",
            is_staff=True,
        )
        self.user = user_model.objects.create_user(
            "research-user", "reader@example.com", "DemoTest123!"
        )
        category = FranchiseCategory.objects.create(name="Retail", slug="retail")
        franchise = Franchise.objects.create(
            name="Test Brand",
            slug="test-brand",
            category=category,
            short_description="Test",
        )
        self.workspace = FranchiseResearchWorkspace.objects.create(
            franchise=franchise,
            normalization_id="73639155-0e73-4a8c-9901-6b49859ad16b",
            plan_run_id="896c0284-f762-457b-b6fb-98bba365673b",
            target_country="PL",
            depth="directory_plus",
            profile_id="PL:L2:v1",
            iteration=14,
            normalized_reference="/tmp/normalized.json",
            normalized_sha256="a" * 64,
            quality_score=72,
            planned_tasks=2,
            evaluated_tasks=1,
            planned_fields=2,
            source_count=3,
            claim_count=2,
            normalized_values_count=1,
            stage_summary=[
                {
                    "key": "plan",
                    "label": "Plan",
                    "status": "complete",
                    "summary": "2 zadania",
                    "usage": {
                        "api_calls": 1,
                        "total_tokens": 100,
                        "estimated_cost_usd": "0.01000000",
                    },
                },
                {
                    "key": "review",
                    "label": "Human Review",
                    "status": "current",
                    "summary": "decyzje",
                    "usage": None,
                },
                {
                    "key": "import",
                    "label": "Publikacja",
                    "status": "pending",
                    "summary": "po zatwierdzeniu",
                    "usage": None,
                },
            ],
            cost_summary={
                "api_calls": 1,
                "total_tokens": 100,
                "estimated_cost_usd": "0.01000000",
            },
        )
        self.proposed = FranchiseResearchReviewField.objects.create(
            workspace=self.workspace,
            normalized_field_id="normalized-field-1234567890abcdef",
            task_id="task-1",
            task_title="Brand identity",
            target_field="brand.name",
            requirement="critical",
            priority="critical",
            pipeline_status="normalized",
            checker_status="verified",
            proposed_values=[{"display": "Test Brand", "canonical_text": "Test Brand"}],
            evidence=[
                {
                    "quote": "Test Brand is the official name.",
                    "source_title": "Official page",
                    "url": "https://example.com",
                }
            ],
            sort_order=1,
        )
        self.missing = FranchiseResearchReviewField.objects.create(
            workspace=self.workspace,
            task_id="task-2",
            task_title="Investment",
            target_field="investment.total_low",
            requirement="required",
            priority="high",
            pipeline_status="missing",
            sort_order=2,
        )

    def test_workbench_is_staff_only_and_renders_friendly_review(self):
        detail = reverse(
            "backoffice:research_workbench_detail",
            args=[self.workspace.workspace_id],
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(detail).status_code, 302)
        self.client.force_login(self.staff)
        response = self.client.get(detail)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Etapy procesu")
        self.assertContains(response, "Nazwa marki")
        self.assertContains(response, "Zapisz i zaakceptuj")
        self.assertContains(response, "Dodaj dokument")

    def test_one_click_decisions_require_post_and_are_audited(self):
        action = reverse(
            "backoffice:research_workbench_field_action",
            args=[self.workspace.workspace_id, self.proposed.pk, "accept"],
        )
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(action).status_code, 405)
        self.assertEqual(self.client.post(action).status_code, 302)
        self.proposed.refresh_from_db()
        self.assertEqual(
            self.proposed.decision,
            FranchiseResearchReviewField.DECISION_ACCEPTED,
        )
        self.assertEqual(self.proposed.decided_by, self.staff)
        self.assertTrue(
            self.workspace.events.filter(event_type="field_decision").exists()
        )

    def test_researcher_can_fill_a_gap_and_accept_it_in_one_submit(self):
        edit = reverse(
            "backoffice:research_workbench_field_edit",
            args=[self.workspace.workspace_id, self.missing.pk],
        )
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.post(
                edit,
                {
                    "reviewer_value": "250 000 PLN",
                    "reviewer_note": "Z umowy przekazanej przez sieć.",
                },
            ).status_code,
            302,
        )
        self.missing.refresh_from_db()
        self.assertEqual(
            self.missing.decision,
            FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED,
        )
        self.assertEqual(self.missing.reviewer_value, "250 000 PLN")

    def test_approval_with_gaps_requires_explicit_acknowledgement(self):
        decision = reverse(
            "backoffice:research_workbench_decision",
            args=[self.workspace.workspace_id, "approve_with_gaps"],
        )
        self.client.force_login(self.staff)
        self.client.post(decision, {"reviewer_notes": "Incomplete"})
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.status, FranchiseResearchWorkspace.STATUS_REVIEW)
        self.client.post(
            decision,
            {"reviewer_notes": "Incomplete", "acknowledge_gaps": "on"},
        )
        self.workspace.refresh_from_db()
        self.assertEqual(
            self.workspace.status,
            FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS,
        )
        self.assertEqual(self.workspace.reviewed_by, self.staff)

    def test_private_document_upload_and_download_are_staff_only(self):
        upload = reverse(
            "backoffice:research_workbench_document_upload",
            args=[self.workspace.workspace_id],
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            upload,
            {
                "file": SimpleUploadedFile(
                    "agreement.pdf", b"private agreement", content_type="application/pdf"
                ),
                "document_type": "contract",
                "access_level": "restricted",
                "notes": "Poufny wzór.",
            },
        )
        self.assertEqual(response.status_code, 302)
        document = FranchiseResearchDocument.objects.get(workspace=self.workspace)
        self.addCleanup(document.file.delete, False)
        download = reverse(
            "backoffice:research_workbench_document_download",
            args=[self.workspace.workspace_id, document.pk],
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(download).status_code, 302)
        self.client.force_login(self.staff)
        response = self.client.get(download)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="agreement.pdf"')
