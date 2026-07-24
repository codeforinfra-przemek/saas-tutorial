from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization
from billing.models import OrganizationSubscription, Plan
from franchises.models import (
    Franchise,
    FranchiseCategory,
    FranchiseResearchDocument,
    FranchiseResearchCampaign,
    FranchiseResearchJob,
    FranchiseResearchLaunch,
    FranchiseResearchReviewField,
    FranchiseResearchWorkspace,
)
from franchises import research_benchmark as _research_benchmark  # noqa: F401
from saashome.exception_filters import CredentialSafeExceptionReporterFilter

from datacollector.benchmark import (
    create_gold_set,
    create_submission,
    load_benchmark_spec,
    load_gold_set,
    load_submission,
    save_gold_set,
    save_submission,
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

    def test_debug_report_masks_database_connection_url(self):
        request = RequestFactory().get("/")
        request.META["DATABASE_URL"] = "postgresql://user:secret@example.test/db"

        safe_meta = CredentialSafeExceptionReporterFilter().get_safe_request_meta(
            request
        )

        self.assertEqual(safe_meta["DATABASE_URL"], "********************")
        self.assertNotIn("secret", safe_meta["DATABASE_URL"])

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

    def test_staff_can_queue_first_research_run_from_backoffice(self):
        create_url = reverse("backoffice:research_launch_create")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(create_url).status_code, 302)
        self.client.force_login(self.staff)
        response = self.client.post(
            create_url,
            {
                "franchise": self.workspace.franchise_id,
                "profile_id": "PL:L1",
                "known_legal_name": "Test Brand sp. z o.o.",
                "known_official_website": "https://example.com/",
                "max_cost_usd": "1.25",
                "initial_task_limit": 5,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
                "acknowledge_paid": "on",
            },
        )
        launch = FranchiseResearchLaunch.objects.get()
        self.assertRedirects(
            response,
            reverse("backoffice:research_launch_detail", args=[launch.launch_id]),
        )
        self.assertEqual(launch.status, FranchiseResearchLaunch.STATUS_QUEUED)
        self.assertEqual(launch.profile_id, "PL:L1")
        self.assertEqual(launch.configuration["max_cost_usd"], "1.25")
        detail = self.client.get(
            reverse("backoffice:research_launch_detail", args=[launch.launch_id])
        )
        self.assertContains(detail, "pierwszy run od Plannera do Workbencha")

    def test_staff_can_create_and_monitor_budgeted_batch_campaign(self):
        first = Franchise.objects.create(
            name="Batch Alpha",
            slug="batch-alpha",
            category=self.workspace.franchise.category,
            short_description="Batch fixture",
        )
        second = Franchise.objects.create(
            name="Batch Beta",
            slug="batch-beta",
            category=self.workspace.franchise.category,
            short_description="Batch fixture",
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("backoffice:research_campaign_create"),
            {
                "name": "PL:L1 test batch",
                "description": "Controlled catalogue saturation",
                "franchises": [first.pk, second.pk],
                "profile_id": "PL:L1",
                "max_cost_usd": "1.25",
                "max_total_cost_usd": "2.50",
                "max_concurrent_runs": 1,
                "initial_task_limit": 5,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
                "acknowledge_paid": "on",
            },
        )
        campaign = FranchiseResearchCampaign.objects.get()
        self.assertRedirects(
            response,
            reverse(
                "backoffice:research_campaign_detail",
                args=[campaign.campaign_id],
            ),
        )
        self.assertEqual(campaign.launches.count(), 2)
        self.assertEqual(campaign.reserved_cost_usd, Decimal("2.50"))
        self.assertEqual(
            list(campaign.launches.order_by("campaign_position").values_list("franchise", flat=True)),
            [first.pk, second.pk],
        )
        detail = self.client.get(
            reverse("backoffice:research_campaign_detail", args=[campaign.campaign_id])
        )
        self.assertContains(detail, "PL:L1 test batch")
        self.assertContains(detail, "Batch Alpha")
        status = self.client.get(
            reverse("backoffice:research_campaign_status", args=[campaign.campaign_id])
        ).json()
        self.assertEqual(status["counts"]["queued"], 2)
        self.assertEqual(status["estimated_cost_usd"], "0")

    def _benchmark_artifacts(self, root):
        spec = load_benchmark_spec()
        save_gold_set(root / "pl-l1-gold-v1.json", create_gold_set(spec))
        save_submission(
            root / "pl-l1-manual-v1.json",
            create_submission(spec, method="researcher_chatgpt"),
        )
        save_submission(
            root / "pl-l1-pipeline-v1.json",
            create_submission(spec, method="pipeline"),
        )
        return spec

    def _create_benchmark_franchises(self, spec):
        franchises = []
        for definition in spec.brands:
            franchises.append(
                Franchise.objects.create(
                    name=definition.name,
                    slug=definition.slug,
                    category=self.workspace.franchise.category,
                    short_description="Benchmark fixture",
                    is_active=definition.slug != "north-fish",
                )
            )
        return franchises

    def test_benchmark_workbench_creates_exact_paid_cohort_after_confirmation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            spec = self._benchmark_artifacts(root)
            self._create_benchmark_franchises(spec)
            self.client.force_login(self.staff)
            with override_settings(RESEARCH_BENCHMARK_DIR=root):
                overview = self.client.get(reverse("backoffice:research_benchmark"))
                self.assertContains(overview, "Uruchom dokładną kohortę benchmarkową")
                self.assertContains(overview, "North Fish · nieaktywna, tylko research")
                response = self.client.post(
                    reverse("backoffice:research_benchmark_campaign_create"),
                    {
                        "max_cost_usd": "0.75",
                        "max_concurrent_runs": "1",
                        "acknowledge_scope": "on",
                        "acknowledge_paid": "on",
                    },
                )
        campaign = FranchiseResearchCampaign.objects.get()
        self.assertRedirects(
            response,
            reverse(
                "backoffice:research_campaign_detail",
                args=[campaign.campaign_id],
            ),
        )
        self.assertEqual(campaign.profile_id, "PL:L1")
        self.assertEqual(campaign.reserved_cost_usd, Decimal("7.50"))
        self.assertEqual(campaign.launches.count(), 10)
        self.assertEqual(
            set(campaign.launches.values_list("franchise__slug", flat=True)),
            {brand.slug for brand in spec.brands},
        )
        self.assertFalse(Franchise.objects.get(slug="north-fish").is_active)

    def test_benchmark_campaign_requires_explicit_paid_confirmation(self):
        spec = load_benchmark_spec()
        self._create_benchmark_franchises(spec)
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("backoffice:research_benchmark_campaign_create"),
            {
                "max_cost_usd": "0.75",
                "max_concurrent_runs": "1",
                "acknowledge_scope": "on",
            },
        )
        self.assertRedirects(response, reverse("backoffice:research_benchmark"))
        self.assertFalse(FranchiseResearchCampaign.objects.exists())

    def test_benchmark_workbench_edits_validated_artifacts_without_json_work(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._benchmark_artifacts(root)
            self.client.force_login(self.staff)
            with override_settings(RESEARCH_BENCHMARK_DIR=root):
                overview = self.client.get(reverse("backoffice:research_benchmark"))
                self.assertEqual(overview.status_code, 200)
                self.assertContains(overview, "Macierz 10 marek × 20 pól")
                detail = self.client.get(
                    reverse("backoffice:research_benchmark_brand", args=["zabka"])
                )
                self.assertEqual(detail.status_code, 200)
                self.assertContains(detail, "Niezależny Gold Set")
                self.assertContains(detail, "Researcher + ChatGPT")
                response = self.client.post(
                    reverse(
                        "backoffice:research_benchmark_field_update",
                        args=["zabka", "gold"],
                    ),
                    {
                        "target_field": "brand.name",
                        "status": "found",
                        "canonical_value": "Żabka",
                        "source_url": "https://www.zabka.pl/",
                        "source_type": "official",
                        "observed_at": "2026-07-22",
                        "valid_as_of": "",
                        "notes": "Źródło oficjalne",
                    },
                )
            self.assertEqual(response.status_code, 302)
            gold = load_gold_set(root / "pl-l1-gold-v1.json")
            field = gold.brands[0].fields[0]
            self.assertEqual(field.status, "found")
            self.assertEqual(field.canonical_value, "Żabka")

    def test_blind_gold_workbench_never_loads_or_displays_submissions(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._benchmark_artifacts(root)
            self.client.force_login(self.staff)
            with override_settings(RESEARCH_BENCHMARK_DIR=root):
                overview = self.client.get(
                    reverse("backoffice:research_benchmark_gold")
                )
                self.assertEqual(overview.status_code, 200)
                self.assertContains(overview, "Tryb zaślepiony")
                self.assertNotContains(overview, "Researcher + ChatGPT")
                self.assertNotContains(overview, "Pipeline")
                detail = self.client.get(
                    reverse(
                        "backoffice:research_benchmark_gold_brand",
                        args=["zabka"],
                    )
                )
                self.assertEqual(detail.status_code, 200)
                self.assertContains(detail, "tylko dane referencyjne")
                self.assertNotContains(detail, "Researcher + ChatGPT")
                self.assertNotContains(detail, "Pipeline")
                response = self.client.post(
                    reverse(
                        "backoffice:research_benchmark_field_update",
                        args=["zabka", "gold"],
                    ),
                    {
                        "return_to": "gold",
                        "target_field": "brand.name",
                        "status": "found",
                        "canonical_value": "Żabka",
                        "source_url": "https://www.zabka.pl/",
                        "source_type": "official",
                        "observed_at": "2026-07-23",
                        "valid_as_of": "",
                        "notes": "Blind source",
                    },
                )
            self.assertRedirects(
                response,
                reverse(
                    "backoffice:research_benchmark_gold_brand",
                    args=["zabka"],
                ),
            )

    def test_gold_can_be_staged_as_pending_workbench_proposal_with_provenance(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._benchmark_artifacts(root)
            franchise = Franchise.objects.create(
                name="Żabka",
                slug="zabka",
                category=self.workspace.franchise.category,
                short_description="Benchmark promotion fixture",
            )
            workspace = FranchiseResearchWorkspace.objects.create(
                franchise=franchise,
                normalization_id="23fc94a4-8a79-42a4-a879-9c8588bb313f",
                plan_run_id="3d0f8781-ec74-499d-8b27-279303a8be97",
                target_country="PL",
                depth="directory_basic",
                profile_id="PL:L1:v2",
                iteration=1,
                normalized_reference="/tmp/zabka-normalized.json",
                normalized_sha256="a" * 64,
            )
            review_field = FranchiseResearchReviewField.objects.create(
                workspace=workspace,
                task_id="task-brand",
                task_title="Brand",
                target_field="brand.name",
                pipeline_status="missing",
            )
            comparison_field = FranchiseResearchReviewField.objects.create(
                workspace=workspace,
                task_id="task-brand",
                task_title="Brand",
                target_field="brand.category",
                pipeline_status="normalized",
                proposed_values=[
                    {
                        "display": "Kategoria pipeline",
                        "canonical_text": "Kategoria pipeline",
                    }
                ],
            )
            self.client.force_login(self.staff)
            with override_settings(RESEARCH_BENCHMARK_DIR=root):
                self.client.post(
                    reverse(
                        "backoffice:research_benchmark_field_update",
                        args=["zabka", "gold"],
                    ),
                    {
                        "return_to": "gold",
                        "target_field": "brand.name",
                        "status": "found",
                        "canonical_value": "Żabka",
                        "source_url": "https://www.zabka.pl/",
                        "source_type": "official",
                        "observed_at": "2026-07-23",
                        "valid_as_of": "",
                        "notes": "Blind source",
                    },
                )
                self.client.post(
                    reverse(
                        "backoffice:research_benchmark_field_update",
                        args=["zabka", "gold"],
                    ),
                    {
                        "return_to": "gold",
                        "target_field": "brand.category",
                        "status": "found",
                        "canonical_value": "Convenience retail",
                        "source_url": "https://www.zabka.pl/",
                        "source_type": "official",
                        "observed_at": "2026-07-23",
                        "valid_as_of": "",
                        "notes": "Gold comparison",
                    },
                )
                preview = self.client.get(
                    reverse(
                        "backoffice:research_benchmark_gold_promote",
                        args=["zabka"],
                    )
                )
                self.assertEqual(preview.status_code, 200)
                self.assertContains(preview, "Przenieś Gold do Workbencha")
                response = self.client.post(
                    reverse(
                        "backoffice:research_benchmark_gold_promote",
                        args=["zabka"],
                    ),
                    {
                        "workspace_id": str(workspace.workspace_id),
                        "gold_sha256": preview.context["gold_sha256"],
                        "selected_field_ids": [
                            str(review_field.pk),
                            str(comparison_field.pk),
                        ],
                    },
                )
            self.assertRedirects(
                response,
                reverse(
                    "backoffice:research_workbench_detail",
                    args=[workspace.workspace_id],
                ),
            )
            review_field.refresh_from_db()
            self.assertEqual(
                review_field.decision,
                FranchiseResearchReviewField.DECISION_PENDING,
            )
            self.assertEqual(review_field.proposed_display, "Żabka")
            self.assertEqual(
                review_field.proposed_values[0]["provenance"],
                "benchmark_gold_ai_proxy",
            )
            self.assertTrue(
                workspace.events.filter(
                    event_type="benchmark_gold_staged",
                    metadata__auto_approved=False,
                ).exists()
            )
            comparison_field.refresh_from_db()
            self.assertEqual(comparison_field.proposed_display, "Kategoria pipeline")
            self.assertEqual(
                comparison_field.decision,
                FranchiseResearchReviewField.DECISION_PENDING,
            )
            self.assertEqual(
                comparison_field.evidence[-1]["benchmark_value"],
                "Convenience retail",
            )

    def test_campaign_exporter_populates_pipeline_from_human_review(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self._benchmark_artifacts(root)
            zabka = Franchise.objects.create(
                name="Żabka",
                slug="zabka",
                category=self.workspace.franchise.category,
                short_description="Benchmark fixture",
            )
            workspace = FranchiseResearchWorkspace.objects.create(
                franchise=zabka,
                normalization_id="90e1c2f4-b919-491d-bef5-30727e9096e4",
                plan_run_id="9be7db7f-6871-4867-a90b-2729a676968d",
                target_country="PL",
                depth="directory_basic",
                profile_id="PL:L1:v2",
                iteration=1,
                normalized_reference="/tmp/zabka-normalized.json",
                normalized_sha256="b" * 64,
                planned_tasks=7,
                evaluated_tasks=7,
                planned_fields=20,
                reviewed_at=timezone.now(),
            )
            FranchiseResearchReviewField.objects.create(
                workspace=workspace,
                task_id="task-brand",
                task_title="Brand",
                target_field="brand.name",
                pipeline_status="normalized",
                proposed_values=[{"display": "Żabka", "canonical_text": "Żabka"}],
                evidence=[{"url": "https://www.zabka.pl/"}],
                decision=FranchiseResearchReviewField.DECISION_ACCEPTED,
                decided_at=timezone.now(),
            )
            campaign = FranchiseResearchCampaign.objects.create(
                name="Benchmark PL:L1",
                profile_id="PL:L1",
                status=FranchiseResearchCampaign.STATUS_COMPLETED,
                max_total_cost_usd=Decimal("2.00"),
                reserved_cost_usd=Decimal("1.00"),
            )
            FranchiseResearchLaunch.objects.create(
                campaign=campaign,
                campaign_position=1,
                franchise=zabka,
                profile_id="PL:L1",
                status=FranchiseResearchLaunch.STATUS_SUCCEEDED,
                result_workspace=workspace,
                started_at=timezone.now() - timedelta(minutes=8),
                completed_at=timezone.now(),
                cost_summary={"estimated_cost_usd": "0.82"},
            )
            self.client.force_login(self.staff)
            with override_settings(RESEARCH_BENCHMARK_DIR=root):
                response = self.client.post(
                    reverse("backoffice:research_benchmark_export"),
                    {"campaign_id": str(campaign.campaign_id)},
                )
            self.assertEqual(response.status_code, 302)
            pipeline = load_submission(root / "pl-l1-pipeline-v1.json")
            brand = pipeline.brands[0]
            self.assertEqual(brand.tasks_attempted, 7)
            self.assertEqual(brand.known_cost_usd, Decimal("0.82"))
            self.assertEqual(brand.fields[0].proposed_value, "Żabka")
            self.assertEqual(
                brand.fields[0].review_decision,
                "accepted_unchanged",
            )
            self.assertEqual(pipeline.export_history[0].campaign_id, str(campaign.campaign_id))
            self.assertEqual(pipeline.export_history[0].exported_by, self.staff.username)

    def test_benchmark_artifact_helpers_unpack_storage_lineage_hashes(self):
        observed_at = timezone.now()
        launch = SimpleNamespace(
            sources_reference="/tmp/sources.json",
            normalized_reference="/tmp/normalized.json",
            extractions_reference="/tmp/extractions.json",
        )
        search = SimpleNamespace(
            sources=[
                SimpleNamespace(
                    source_id="source-1",
                    canonical_url="https://example.com/franczyza",
                    source_type=SimpleNamespace(value="official"),
                    discovered_at=observed_at,
                )
            ]
        )
        normalized = SimpleNamespace(
            normalized_values=[
                SimpleNamespace(
                    normalized_value_id="value-1",
                    claim_ids=["claim-1"],
                )
            ]
        )
        extraction = SimpleNamespace(
            claims=[
                SimpleNamespace(
                    claim_id="claim-1",
                    effective_date_text="obowiązuje od 2026-07-01",
                    as_of_text="",
                )
            ]
        )
        with (
            patch.object(
                _research_benchmark,
                "load_search_results",
                return_value=(search, "a" * 64),
            ),
            patch.object(
                _research_benchmark,
                "load_normalizer_results",
                return_value=(normalized, "b" * 64),
            ),
            patch.object(
                _research_benchmark,
                "load_extraction_results",
                return_value=(extraction, "c" * 64),
            ),
        ):
            sources = _research_benchmark._source_metadata(launch)
            valid_as_of = _research_benchmark._valid_as_of_metadata(launch)
        self.assertEqual(
            sources["source-1"]["url"],
            "https://example.com/franczyza",
        )
        self.assertEqual(sources["source-1"]["source_type"], "official")
        self.assertEqual(str(valid_as_of["value-1"]), "2026-07-01")

    def test_batch_campaign_requires_explicit_override_for_existing_research(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("backoffice:research_campaign_create"),
            {
                "name": "Protected batch",
                "franchises": [self.workspace.franchise_id],
                "profile_id": "PL:L1",
                "max_cost_usd": "1.25",
                "max_total_cost_usd": "1.25",
                "max_concurrent_runs": 1,
                "initial_task_limit": 5,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
                "acknowledge_paid": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Istniejący research wykryto")
        self.assertFalse(FranchiseResearchCampaign.objects.exists())

    def test_staff_can_retry_failed_launch_without_clearing_artifacts(self):
        launch = FranchiseResearchLaunch.objects.create(
            franchise=self.workspace.franchise,
            profile_id="PL:L1",
            status=FranchiseResearchLaunch.STATUS_FAILED,
            plan_reference="/tmp/plan.json",
            extractions_reference="/tmp/extractions.json",
            error_code="ResearchLaunchError",
            error_message="Koszt nieznany",
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("backoffice:research_launch_retry", args=[launch.launch_id])
        )
        self.assertRedirects(
            response,
            reverse("backoffice:research_launch_detail", args=[launch.launch_id]),
        )
        launch.refresh_from_db()
        self.assertEqual(launch.status, FranchiseResearchLaunch.STATUS_QUEUED)
        self.assertEqual(launch.plan_reference, "/tmp/plan.json")
        self.assertEqual(launch.extractions_reference, "/tmp/extractions.json")
        self.assertEqual(launch.error_message, "")

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

    def test_one_click_decision_returns_json_for_async_workbench(self):
        action = reverse(
            "backoffice:research_workbench_field_action",
            args=[self.workspace.workspace_id, self.proposed.pk, "accept"],
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            action,
            {"field_version": self.proposed.updated_at.isoformat()},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["field"]["decision"], "accepted")
        self.assertEqual(payload["counts"]["accepted"], 1)
        self.assertEqual(payload["counts"]["reviewed"], 1)
        self.assertEqual(payload["event"]["actor"], self.staff.username)

    def test_async_field_decision_rejects_stale_browser_state(self):
        action = reverse(
            "backoffice:research_workbench_field_action",
            args=[self.workspace.workspace_id, self.proposed.pk, "accept"],
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            action,
            {"field_version": "2020-01-01T00:00:00+00:00"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.json()["ok"])
        self.proposed.refresh_from_db()
        self.assertEqual(self.proposed.decision, "pending")

    def test_async_field_edit_updates_value_and_review_counts(self):
        edit = reverse(
            "backoffice:research_workbench_field_edit",
            args=[self.workspace.workspace_id, self.missing.pk],
        )
        self.client.force_login(self.staff)
        response = self.client.post(
            edit,
            {
                "field_version": self.missing.updated_at.isoformat(),
                "reviewer_value": "250 000 PLN",
                "reviewer_note": "Potwierdzone ręcznie.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["field"]["decision"], "accepted_edited")
        self.assertEqual(payload["field"]["effective_value"], "250 000 PLN")
        self.assertEqual(payload["counts"]["edited"], 1)

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

    def test_finalization_is_staff_only_post_and_queues_durable_job(self):
        finalize_url = reverse(
            "backoffice:research_workbench_finalize",
            args=[self.workspace.workspace_id],
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(finalize_url).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(finalize_url).status_code, 405)
        result = SimpleNamespace(
            job_id="b5f2766f-be59-58b7-86fa-7820df9cdf0e",
        )
        with patch(
            "backoffice.views.queue_research_job",
            return_value=result,
        ) as queue:
            response = self.client.post(finalize_url)
        self.assertRedirects(
            response,
            reverse(
                "backoffice:research_workbench_detail",
                args=[self.workspace.workspace_id],
            ),
        )
        self.assertEqual(queue.call_args.kwargs["requested_by"], self.staff)
        self.assertEqual(
            queue.call_args.kwargs["kind"],
            FranchiseResearchJob.KIND_FINALIZE,
        )

    def test_finalization_queue_error_returns_to_workbench(self):
        finalize_url = reverse(
            "backoffice:research_workbench_finalize",
            args=[self.workspace.workspace_id],
        )
        self.client.force_login(self.staff)
        with patch(
            "backoffice.views.queue_research_job",
            side_effect=ValueError("queue failure"),
        ):
            response = self.client.post(finalize_url)
        self.assertRedirects(
            response,
            reverse(
                "backoffice:research_workbench_detail",
                args=[self.workspace.workspace_id],
            ),
        )

    def test_finalization_post_does_not_wait_for_worker(self):
        finalize_url = reverse(
            "backoffice:research_workbench_finalize",
            args=[self.workspace.workspace_id],
        )
        self.client.force_login(self.staff)
        result = SimpleNamespace(job_id="b5f2766f-be59-58b7-86fa-7820df9cdf0e")
        with patch(
            "backoffice.views.queue_research_job",
            return_value=result,
        ):
            response = self.client.post(
                finalize_url,
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 302)

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

    def test_staff_can_queue_a_bounded_paid_job_from_friendly_form(self):
        queue_url = reverse(
            "backoffice:research_workbench_job_queue",
            args=[self.workspace.workspace_id],
        )
        self.client.force_login(self.staff)
        with patch("backoffice.views.queue_research_job") as queue:
            queue.return_value.get_kind_display.return_value = "Kontynuuj research"
            response = self.client.post(
                queue_url,
                {
                    "kind": "loop",
                    "policy": "advance",
                    "max_cost_usd": "0.75",
                    "max_rounds": "1",
                    "normalize_incomplete": "on",
                    "max_search_calls": "8",
                    "max_extractor_api_calls": "12",
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(queue.call_args.kwargs["kind"], "loop")
        self.assertEqual(
            queue.call_args.kwargs["configuration"],
            {
                "policy": "advance",
                "max_cost_usd": "0.75",
                "max_rounds": 1,
                "normalize_incomplete": True,
                "max_search_calls": 8,
                "max_extractor_api_calls": 12,
            },
        )

    def test_job_status_and_cancel_are_staff_only(self):
        job = FranchiseResearchJob.objects.create(
            workspace=self.workspace,
            kind=FranchiseResearchJob.KIND_LOOP,
            input_reference="/tmp/check.json",
            input_sha256="b" * 64,
            configuration={},
            requested_by=self.staff,
        )
        status_url = reverse(
            "backoffice:research_workbench_job_status",
            args=[self.workspace.workspace_id, job.job_id],
        )
        cancel_url = reverse(
            "backoffice:research_workbench_job_cancel",
            args=[self.workspace.workspace_id, job.job_id],
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(status_url).status_code, 302)
        self.client.force_login(self.staff)
        response = self.client.get(status_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(self.client.get(cancel_url).status_code, 405)
        self.assertEqual(self.client.post(cancel_url).status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, FranchiseResearchJob.STATUS_CANCELLED)

    def test_manual_value_can_reference_private_document_without_ai_processing(self):
        document = FranchiseResearchDocument.objects.create(
            workspace=self.workspace,
            file="fixture/agreement.pdf",
            original_name="agreement.pdf",
            document_type=FranchiseResearchDocument.TYPE_CONTRACT,
            access_level=FranchiseResearchDocument.ACCESS_RESTRICTED,
            size_bytes=100,
            sha256="c" * 64,
            uploaded_by=self.staff,
        )
        edit = reverse(
            "backoffice:research_workbench_field_edit",
            args=[self.workspace.workspace_id, self.missing.pk],
        )
        self.client.force_login(self.staff)
        self.client.post(
            edit,
            {
                "reviewer_value": "250 000 PLN",
                "reviewer_note": "Potwierdzone w poufnej umowie.",
                "supporting_documents": [str(document.pk)],
            },
        )
        self.missing.refresh_from_db()
        document.refresh_from_db()
        self.assertEqual(
            list(self.missing.supporting_documents.all()),
            [document],
        )
        self.assertEqual(document.status, FranchiseResearchDocument.STATUS_READY)
