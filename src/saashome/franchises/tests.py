import hashlib
import json
import uuid
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.db import IntegrityError, transaction
from django.test.utils import CaptureQueriesContext

from .research_import import _resolved, import_franchise_research
from .research_workbench import create_research_workspace
from .research_finalizer import finalize_research_workspace
from .research_auto_review import auto_review_l1_workspace
from .research_fields import field_metadata, profile_info
from .research_jobs import (
    ResearchCommandResult,
    ResearchJobError,
    build_research_command,
    claim_next_job,
    process_research_job,
    queue_research_job,
)
from .research_launches import (
    _combined_usage,
    _normalizer_allows_publication,
    _usage_with_floor,
    _process_hybrid_l1_discovery,
    _recover_paid_search_artifact,
    _run_stage,
    ResearchLaunchError,
    claim_next_launch,
    process_research_launch,
    queue_research_launch,
)
from .research_campaigns import (
    cancel_research_campaign,
    create_research_campaign,
    sync_campaign,
)
from .views import filtered_directory_franchises

from datacollector.agents.reviewer import HumanReviewer
from datacollector.schemas import (
    HumanReviewDecision,
    NormalizerMode,
    NormalizerStrategySource,
)
from datacollector.tests import test_normalizer as normalizer_fixtures

from .models import (
    Franchise,
    FranchiseCategory,
    FranchiseResearchArtifact,
    FranchiseResearchCitation,
    FranchiseResearchCampaign,
    FranchiseResearchClaim,
    FranchiseResearchField,
    FranchiseResearchImport,
    FranchiseResearchDocument,
    FranchiseResearchEditorialDecision,
    FranchiseResearchFinalization,
    FranchiseResearchJob,
    FranchiseResearchLaunch,
    FranchiseResearchPublishedField,
    FranchiseResearchReviewField,
    FranchiseResearchSource,
    FranchiseResearchTask,
    FranchiseResearchValue,
    FranchiseResearchWorkspace,
)


class L1AutoReviewPolicyTests(TestCase):
    def setUp(self):
        category = FranchiseCategory.objects.create(name="Auto L1", slug="auto-l1")
        franchise = Franchise.objects.create(
            name="Auto Review Brand",
            slug="auto-review-brand",
            category=category,
            short_description="Policy fixture",
        )
        self.workspace = FranchiseResearchWorkspace.objects.create(
            franchise=franchise,
            normalization_id=uuid.uuid4(),
            plan_run_id=uuid.uuid4(),
            target_country="PL",
            depth="due_diligence",
            profile_id="PL:L1:v2",
            iteration=1,
            normalized_reference="/tmp/not-needed-for-policy.json",
            normalized_sha256="a" * 64,
            planned_fields=20,
        )

    def test_only_safe_verified_typed_source_is_auto_accepted(self):
        now = timezone.now()
        safe = FranchiseResearchReviewField.objects.create(
            workspace=self.workspace,
            task_id="scope.brand_identity",
            task_title="Identity",
            target_field="brand.name",
            pipeline_status="needs_review",
            checker_status="partial",
            proposed_values=[
                {
                    "display": "Auto Review Brand",
                    "canonical_text": "Auto Review Brand",
                    "needs_corroboration": False,
                }
            ],
            evidence=[
                {
                    "url": "https://example.com/franchise",
                    "source_type": "official",
                    "discovered_at": now.isoformat(),
                    "quote": "Auto Review Brand",
                }
            ],
        )
        financial = FranchiseResearchReviewField.objects.create(
            workspace=self.workspace,
            task_id="fdd07.initial_investment",
            task_title="Investment",
            target_field="investment.total_low",
            pipeline_status="normalized",
            checker_status="verified",
            proposed_values=[
                {
                    "display": "100000 PLN",
                    "canonical_text": "100000",
                    "needs_corroboration": False,
                }
            ],
            evidence=[
                {
                    "url": "https://example.com/franchise",
                    "source_type": "official",
                    "discovered_at": now.isoformat(),
                }
            ],
        )
        website = FranchiseResearchReviewField.objects.create(
            workspace=self.workspace,
            task_id="scope.brand_identity",
            task_title="Identity",
            target_field="websites.franchise_offer",
            pipeline_status="derived_source_metadata",
            checker_status="missing",
            proposed_values=[
                {
                    "display": "https://example.com/franchise",
                    "canonical_text": "https://example.com/franchise",
                    "needs_corroboration": False,
                    "provenance": "official_search_source_metadata",
                }
            ],
            evidence=[
                {
                    "url": "https://example.com/franchise",
                    "source_type": "official",
                    "discovered_at": now.isoformat(),
                    "provenance": "official_search_source_metadata",
                }
            ],
        )
        missing = FranchiseResearchReviewField.objects.create(
            workspace=self.workspace,
            task_id="fdd06.other_fees",
            task_title="Fees",
            target_field="fees.marketing",
            pipeline_status="missing",
            checker_status="missing",
        )

        summary = auto_review_l1_workspace(self.workspace)

        safe.refresh_from_db()
        financial.refresh_from_db()
        website.refresh_from_db()
        missing.refresh_from_db()
        self.workspace.refresh_from_db()
        self.assertEqual(
            safe.decision,
            FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED,
        )
        self.assertEqual(
            financial.decision,
            FranchiseResearchReviewField.DECISION_PENDING,
        )
        self.assertEqual(
            website.decision,
            FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED,
        )
        self.assertEqual(
            missing.decision,
            FranchiseResearchReviewField.DECISION_DOCUMENTED_GAP,
        )
        self.assertTrue(self.workspace.auto_reviewed)
        self.assertEqual(
            self.workspace.status,
            FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS,
        )
        self.assertEqual(summary["policy_accepted"], 2)
        self.assertEqual(summary["pending_human_review"], 1)

    def test_exact_brand_from_official_source_metadata_is_auto_accepted(self):
        now = timezone.now()
        field = FranchiseResearchReviewField.objects.create(
            workspace=self.workspace,
            task_id="scope.brand_identity",
            task_title="Identity",
            target_field="brand.name",
            pipeline_status="derived_source_metadata",
            checker_status="partial",
            proposed_values=[
                {
                    "display": "Auto Review Brand",
                    "canonical_text": "Auto Review Brand",
                    "needs_corroboration": False,
                    "provenance": "official_search_source_metadata",
                }
            ],
            evidence=[
                {
                    "url": "https://example.com/",
                    "source_type": "official",
                    "discovered_at": now.isoformat(),
                    "quote": "Auto Review Brand — official",
                    "provenance": "official_search_source_metadata",
                }
            ],
        )

        summary = auto_review_l1_workspace(self.workspace)

        field.refresh_from_db()
        self.assertEqual(
            field.decision,
            FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED,
        )
        self.assertEqual(summary["accepted_fields"], ["brand.name"])

    def test_compact_low_risk_normalized_value_can_pass_without_semantic_checker(self):
        now = timezone.now()
        compact = FranchiseResearchReviewField.objects.create(
            workspace=self.workspace,
            task_id="support.training",
            task_title="Training",
            target_field="support.training_program",
            pipeline_status="normalized",
            checker_status="not_reviewed",
            proposed_values=[
                {
                    "display": (
                        "Sieć zapewnia szkolenie startowe dla franczyzobiorcy "
                        "i pracowników."
                    ),
                    "canonical_text": (
                        "Sieć zapewnia szkolenie startowe dla franczyzobiorcy "
                        "i pracowników."
                    ),
                    "needs_corroboration": False,
                    "provenance": "risk_based_low_risk_normalized",
                }
            ],
            evidence=[
                {
                    "url": "https://example.com/franczyza/",
                    "source_type": "official",
                    "discovered_at": now.isoformat(),
                }
            ],
        )
        uncompact = FranchiseResearchReviewField.objects.create(
            workspace=self.workspace,
            task_id="candidate.premises",
            task_title="Premises",
            target_field="candidate.premises_requirements",
            pipeline_status="normalized",
            checker_status="not_reviewed",
            proposed_values=[
                {
                    "display": "lokal 50 m² | centrum miasta | parking",
                    "canonical_text": "lokal 50 m² | centrum miasta | parking",
                    "needs_corroboration": False,
                    "provenance": "risk_based_low_risk_normalized",
                }
            ],
            evidence=[
                {
                    "url": "https://example.com/franczyza/",
                    "source_type": "official",
                    "discovered_at": now.isoformat(),
                }
            ],
        )

        summary = auto_review_l1_workspace(self.workspace)

        compact.refresh_from_db()
        uncompact.refresh_from_db()
        self.assertEqual(
            compact.decision,
            FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED,
        )
        self.assertEqual(
            uncompact.decision,
            FranchiseResearchReviewField.DECISION_PENDING,
        )
        self.assertEqual(
            summary["pending_reasons"][uncompact.target_field],
            "uncompacted_public_text",
        )

    def test_retry_usage_never_drops_below_recorded_floor(self):
        launch = type(
            "Launch",
            (),
            {
                "_usage_floor": {
                    "api_attempts_recorded": 23,
                    "input_tokens": 242680,
                    "output_tokens": 35514,
                    "reasoning_tokens": 11548,
                    "total_tokens": 278194,
                    "tool_calls": 6,
                    "tool_cost_usd": "0.06",
                    "estimated_cost_usd": "1.19941000",
                    "budgeted_cost_usd": "1.19941000",
                    "unknown_cost_attempts": 0,
                    "unknown_cost_reserve_usd": "0",
                    "cost_complete": True,
                }
            },
        )()
        result = _usage_with_floor(
            launch,
            [
                {
                    "usage_totals": {
                        "api_attempts_recorded": 1,
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "reasoning_tokens": 0,
                        "total_tokens": 120,
                        "tool_calls": 0,
                        "tool_cost_usd": "0",
                        "estimated_cost_usd": "0.01",
                    }
                }
            ],
        )
        self.assertEqual(result["estimated_cost_usd"], "1.20941000")
        self.assertEqual(result["total_tokens"], 278314)

    def test_auto_publication_requires_import_eligible_normalizer(self):
        deterministic = type(
            "Normalizer",
            (),
            {
                "normalization_mode": NormalizerMode.PAID,
                "strategy_source": NormalizerStrategySource.DETERMINISTIC,
            },
        )()
        paid_openai = type(
            "Normalizer",
            (),
            {
                "normalization_mode": NormalizerMode.PAID,
                "strategy_source": NormalizerStrategySource.OPENAI,
            },
        )()

        self.assertFalse(_normalizer_allows_publication(deterministic))
        self.assertTrue(_normalizer_allows_publication(paid_openai))

    def test_retry_claim_preserves_first_started_at(self):
        first_started_at = timezone.now() - timedelta(minutes=7)
        launch = FranchiseResearchLaunch.objects.create(
            franchise=self.workspace.franchise,
            profile_id="PL:L1",
            status=FranchiseResearchLaunch.STATUS_QUEUED,
            started_at=first_started_at,
        )

        claimed = claim_next_launch()

        self.assertEqual(claimed.pk, launch.pk)
        self.assertEqual(claimed.started_at, first_started_at)


class PolishCatalogSyncTests(TestCase):
    def test_sync_prunes_only_unresearched_outside_catalog(self):
        category = FranchiseCategory.objects.create(name="Legacy", slug="legacy")
        researched = Franchise.objects.create(
            name="Researched Brand",
            slug="researched-brand",
            category=category,
            short_description="Research exists",
        )
        FranchiseResearchLaunch.objects.create(
            franchise=researched,
            profile_id="PL:L1",
            status=FranchiseResearchLaunch.STATUS_FAILED,
        )
        stale = Franchise.objects.create(
            name="Demo Placeholder",
            slug="demo-placeholder",
            category=category,
            short_description="Remove me",
        )
        retained = Franchise.objects.create(
            name="Catalog Brand",
            slug="catalog-brand",
            category=category,
            short_description="Retain by catalog",
        )
        payload = {
            "schema_version": "1.0.0",
            "country": "PL",
            "snapshot_date": "2026-07-22",
            "record_count": 1,
            "records": [
                {
                    "name": "Catalog Brand",
                    "slug": "catalog-brand",
                    "category_key": "services",
                    "category_name": "Usługi",
                    "short_description": "Wpis katalogowy",
                    "market_status": "listed",
                    "recruitment_status": "listed_offer",
                    "is_active": True,
                    "website_url": "https://example.com/",
                    "website_url_status": "unverified_seed",
                    "sources": [
                        {
                            "url": "https://example.com/listing",
                            "publisher": "Fixture",
                            "source_type": "directory_listing",
                            "observed_at": "2026-07-22",
                            "supports": ["directory_presence"],
                        }
                    ],
                }
            ],
        }
        with TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.json"
            catalog_path.write_text(json.dumps(payload), encoding="utf-8")
            call_command(
                "sync_pl_franchise_catalog",
                catalog=catalog_path,
                backup_dir=Path(directory) / "backups",
                apply=True,
                prune_unresearched=True,
                prune_operational_placeholders=True,
                confirm_prune_unresearched=True,
                verbosity=0,
            )

        self.assertTrue(Franchise.objects.filter(pk=researched.pk).exists())
        self.assertFalse(Franchise.objects.filter(pk=stale.pk).exists())
        retained.refresh_from_db()
        self.assertEqual(retained.market_status, Franchise.MARKET_STATUS_LISTED)


class PublicCredibilityGuardTests(TestCase):
    def setUp(self):
        self.category = FranchiseCategory.objects.create(
            name="Credibility",
            slug="credibility",
        )
        self.demo = Franchise.objects.create(
            name="Demo Credibility Brand",
            slug="demo-credibility-brand",
            category=self.category,
            short_description="Profile containing values that must not be public.",
            min_investment="987654321.00",
            max_investment="987654322.00",
            initial_fee="87654321.00",
            royalty_fee_text="SECRET-DEMO-ROYALTY",
            data_status=Franchise.DATA_STATUS_DEMO,
            rank_score="99.00",
        )

    def test_database_rejects_verified_demo_profile(self):
        self.demo.is_verified = True
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.demo.save(update_fields=["is_verified"])

    def test_public_views_do_not_render_demo_financial_values(self):
        urls = [
            reverse("franchises:list"),
            reverse("franchises:detail", args=[self.demo.slug]),
            reverse("franchises:compare") + f"?ids={self.demo.pk}",
            reverse("franchises:directory"),
        ]

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "987654321")
                self.assertNotContains(response, "87654321")
                self.assertNotContains(response, "SECRET-DEMO-ROYALTY")

    def test_demo_profile_cannot_match_measured_directory_filter(self):
        filters = {
            "q": "",
            "category": "",
            "investment_max": self.demo.min_investment,
            "business_type": "",
            "growth_min": None,
            "revenue_min": None,
            "payback_max": "",
            "financing": "",
            "financials": "",
        }

        results = filtered_directory_franchises(filters)

        self.assertNotIn(self.demo, results)


class FranchiseResearchImportTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        normalizer_fixtures.NormalizerAgentTests.setUpClass()
        cls.fixture = normalizer_fixtures.NormalizerAgentTests(
            "test_paid_normalizer_groups_equivalent_claims_and_records_usage"
        )
        cls.plan = normalizer_fixtures.NormalizerAgentTests.plan
        cls.search = normalizer_fixtures.NormalizerAgentTests.search_results
        cls.extraction = normalizer_fixtures.NormalizerAgentTests.extraction_results
        cls.checker = normalizer_fixtures.NormalizerAgentTests.checker_results
        cls.normalized = cls.fixture._run(
            normalizer_fixtures.FixtureNormalizerLLM(),
            mode=NormalizerMode.PAID,
        )

    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        normalized_path = root / "normalized.json"
        normalized_path.write_text("fixture", encoding="utf-8")
        self.review = HumanReviewer().create_review(
            self.plan,
            self.search,
            self.extraction,
            self.checker,
            self.normalized,
            plan_sha256=normalizer_fixtures.checker_fixtures.PLAN_SHA256,
            search_sha256=normalizer_fixtures.checker_fixtures.SEARCH_SHA256,
            extraction_sha256=normalizer_fixtures.checker_fixtures.EXTRACTION_SHA256,
            check_sha256=normalizer_fixtures.CHECK_SHA256,
            normalized_sha256="e" * 64,
            normalized_reference=str(normalized_path),
            report_reference=str(root / "review.html"),
            decision=HumanReviewDecision.APPROVED,
            reviewer="Fixture reviewer",
            reviewer_notes="Verified fixture import.",
        )
        self.review_path = root / "review.json"
        self.lineage = {
            "review": self.review,
            "review_sha256": "f" * 64,
            "review_path": self.review_path,
            "normalized": self.normalized,
            "normalized_sha256": "e" * 64,
            "normalized_path": normalized_path,
            "plan": self.plan,
            "plan_sha256": normalizer_fixtures.checker_fixtures.PLAN_SHA256,
            "plan_path": root / "plan.json",
            "search": self.search,
            "search_sha256": normalizer_fixtures.checker_fixtures.SEARCH_SHA256,
            "search_path": root / "sources.json",
            "extraction": self.extraction,
            "extraction_sha256": normalizer_fixtures.checker_fixtures.EXTRACTION_SHA256,
            "extraction_path": root / "extractions.json",
            "checker": self.checker,
            "check_sha256": normalizer_fixtures.CHECK_SHA256,
            "check_path": root / "check.json",
        }

    def test_import_is_lossless_relational_and_idempotent(self):
        with patch(
            "franchises.research_import._load_approved_lineage",
            return_value=self.lineage,
        ):
            imported, created = import_franchise_research(self.review_path)
            repeated, repeated_created = import_franchise_research(self.review_path)

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(imported.pk, repeated.pk)
        self.assertEqual(FranchiseResearchImport.objects.count(), 1)
        self.assertEqual(FranchiseResearchArtifact.objects.count(), 6)
        self.assertEqual(FranchiseResearchTask.objects.count(), len(self.plan.tasks))
        self.assertEqual(
            FranchiseResearchField.objects.count(),
            sum(len(task.target_fields) for task in self.plan.tasks),
        )
        self.assertEqual(
            FranchiseResearchValue.objects.count(),
            len(self.normalized.normalized_values),
        )
        self.assertEqual(
            FranchiseResearchSource.objects.count(), len(self.search.sources)
        )
        self.assertEqual(
            FranchiseResearchClaim.objects.count(), len(self.extraction.claims)
        )
        self.assertEqual(
            FranchiseResearchCitation.objects.count(), len(self.extraction.citations)
        )
        self.assertEqual(imported.franchise.data_status, "research_with_gaps")
        self.assertFalse(imported.franchise.is_verified)

    def test_base_import_never_overwrites_public_profile_fields(self):
        category = FranchiseCategory.objects.create(
            name="Publication gate",
            slug="publication-gate",
        )
        franchise = Franchise.objects.create(
            name="Editorial baseline",
            slug="publication-gate-brand",
            category=category,
            short_description="Human supplied baseline",
            website_url="https://baseline.example/",
            royalty_fee_text="Manual royalty",
            data_status=Franchise.DATA_STATUS_EDITOR_VERIFIED,
            is_verified=True,
        )
        with patch(
            "franchises.research_import._load_approved_lineage",
            return_value=self.lineage,
        ):
            import_franchise_research(
                self.review_path,
                franchise_slug=franchise.slug,
            )
        franchise.refresh_from_db()
        self.assertEqual(franchise.name, "Editorial baseline")
        self.assertEqual(franchise.website_url, "https://baseline.example/")
        self.assertEqual(franchise.royalty_fee_text, "Manual royalty")
        self.assertEqual(franchise.data_status, Franchise.DATA_STATUS_EDITOR_VERIFIED)
        self.assertTrue(franchise.is_verified)

    def test_every_planned_field_has_plain_language_metadata(self):
        for task in self.plan.tasks:
            for target_field in task.fields_to_collect or task.target_fields:
                metadata = field_metadata(target_field, task_title=task.title)
                self.assertTrue(metadata.label)
                self.assertGreater(len(metadata.description), 20)
        mapped = field_metadata("investment.total_low")
        self.assertTrue(mapped.appears_on_profile)
        self.assertEqual(mapped.profile_anchor, "investor-snapshot")
        self.assertEqual(
            profile_info("PL:L1:v1")["title"],
            "Poziom 1 — katalogowy",
        )

    def test_repository_relative_review_path_resolves_from_django_directory(self):
        root = Path(self.temporary_directory.name)
        django_directory = root / "src" / "saashome"
        django_directory.mkdir(parents=True)
        review_path = root / "datacollector" / "data" / "review.json"
        review_path.parent.mkdir(parents=True)
        review_path.write_text("{}", encoding="utf-8")

        with (
            patch("franchises.research_import.REPOSITORY_ROOT", root),
            patch("franchises.research_import.Path.cwd", return_value=django_directory),
        ):
            resolved = _resolved("datacollector/data/review.json")

        self.assertEqual(resolved, review_path.resolve())

    def test_unfinalized_base_import_is_not_public(self):
        with patch(
            "franchises.research_import._load_approved_lineage",
            return_value=self.lineage,
        ):
            imported, _ = import_franchise_research(self.review_path)

        response = self.client.get(
            reverse("franchises:research_detail", args=[imported.franchise.slug])
        )

        self.assertEqual(response.status_code, 404)

    def test_workbench_materializes_full_plan_and_is_idempotent(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="workbench-test",
            defaults={"name": "Workbench test"},
        )
        Franchise.objects.create(
            name=self.normalized.brand_name,
            slug="workbench-brand",
            category=category,
            short_description="Workbench fixture",
        )
        loaded = (
            self.normalized,
            "e" * 64,
            self.plan,
            self.search,
            self.extraction,
            self.checker,
        )
        with patch(
            "franchises.research_workbench._load_lineage",
            return_value=loaded,
        ):
            workspace, created = create_research_workspace(
                self.temporary_directory.name + "/normalized.json",
                franchise_slug="workbench-brand",
            )
            repeated, repeated_created = create_research_workspace(
                self.temporary_directory.name + "/normalized.json",
                franchise_slug="workbench-brand",
            )

        expected_fields = sum(
            len(dict.fromkeys(task.fields_to_collect or task.target_fields))
            for task in self.plan.tasks
        )
        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(workspace.pk, repeated.pk)
        self.assertEqual(FranchiseResearchWorkspace.objects.count(), 1)
        self.assertEqual(workspace.review_fields.count(), expected_fields)
        self.assertEqual(
            workspace.review_fields.exclude(proposed_values=[]).count(),
            sum(bool(field.normalized_value_ids) for field in self.normalized.field_results),
        )
        self.assertEqual(workspace.stage_summary[-2]["key"], "review")
        self.assertIn("estimated_cost_usd", workspace.cost_summary)

    def test_workbench_finalization_is_immutable_relational_and_idempotent(self):
        with patch(
            "franchises.research_import._load_approved_lineage",
            return_value=self.lineage,
        ):
            imported, _ = import_franchise_research(self.review_path)
        workspace = FranchiseResearchWorkspace.objects.create(
            franchise=imported.franchise,
            normalization_id=self.normalized.normalization_id,
            plan_run_id=self.normalized.plan_run_id,
            target_country=self.normalized.target_country,
            depth=self.normalized.depth.value,
            iteration=self.normalized.iteration,
            normalized_reference=str(self.lineage["normalized_path"]),
            normalized_sha256=self.lineage["normalized_sha256"],
            status=FranchiseResearchWorkspace.STATUS_APPROVED,
            checker_passed=True,
            scope_complete=True,
            reviewed_by=None,
        )
        reviewer = get_user_model().objects.create_user(
            "finalizer", "finalizer@example.com", "DemoTest123!"
        )
        workspace.reviewed_by = reviewer
        workspace.reviewed_at = timezone.now()
        workspace.reviewer_notes = "Signed editorial release."
        workspace.save(update_fields=["reviewed_by", "reviewed_at", "reviewer_notes"])
        imported_field = FranchiseResearchField.objects.get(
            task__research_import=imported,
            target_field="brand.name",
        )
        accepted = FranchiseResearchReviewField.objects.create(
            workspace=workspace,
            task_id=imported_field.task.task_id,
            task_title=imported_field.task.title,
            target_field=imported_field.target_field,
            pipeline_status="normalized",
            checker_status="verified",
            proposed_values=[{"display": "AI value", "canonical_text": "AI value"}],
            decision=FranchiseResearchReviewField.DECISION_ACCEPTED,
            decided_by=reviewer,
            decided_at=workspace.reviewed_at,
        )
        pending_imported = (
            FranchiseResearchField.objects.filter(
                task__research_import=imported,
                values__isnull=False,
            )
            .exclude(pk=imported_field.pk)
            .distinct()
            .first()
        )
        self.assertIsNotNone(pending_imported)
        pending_public_value = "UNREVIEWED-PROPOSAL-MUST-STAY-INTERNAL"
        pending_value_record = pending_imported.values.first()
        pending_value_record.canonical_text = pending_public_value
        pending_value_record.save(update_fields=["canonical_text"])
        FranchiseResearchReviewField.objects.create(
            workspace=workspace,
            task_id=pending_imported.task.task_id,
            task_title=pending_imported.task.title,
            target_field=pending_imported.target_field,
            pipeline_status="normalized",
            checker_status="verified",
            proposed_values=[
                {
                    "display": pending_public_value,
                    "canonical_text": pending_public_value,
                }
            ],
            decision=FranchiseResearchReviewField.DECISION_PENDING,
            sort_order=1,
        )
        edited = FranchiseResearchReviewField.objects.create(
            workspace=workspace,
            task_id="manual-task",
            task_title="Manual evidence",
            target_field="financials.private_contract_value",
            pipeline_status="missing",
            reviewer_value="250 000 PLN",
            reviewer_note="Value transcribed by the researcher.",
            decision=FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED,
            decided_by=reviewer,
            decided_at=workspace.reviewed_at,
            sort_order=2,
        )
        document = FranchiseResearchDocument.objects.create(
            workspace=workspace,
            file=SimpleUploadedFile(
                "private-agreement.pdf", b"TOP SECRET CONTRACT", content_type="application/pdf"
            ),
            original_name="private-agreement.pdf",
            document_type=FranchiseResearchDocument.TYPE_CONTRACT,
            access_level=FranchiseResearchDocument.ACCESS_RESTRICTED,
            content_type="application/pdf",
            size_bytes=19,
            sha256=hashlib.sha256(b"TOP SECRET CONTRACT").hexdigest(),
            uploaded_by=reviewer,
        )
        self.addCleanup(document.file.delete, False)
        edited.supporting_documents.add(document)

        with patch(
            "franchises.research_finalizer._load_lineage",
            return_value=self.lineage,
        ):
            with CaptureQueriesContext(connection) as queries:
                finalization, created = finalize_research_workspace(
                    workspace, actor=reviewer
                )
            repeated, repeated_created = finalize_research_workspace(workspace, actor=reviewer)

        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(finalization.pk, repeated.pk)
        workspace_lock_query = next(
            item["sql"]
            for item in queries.captured_queries
            if 'FROM "franchises_franchiseresearchworkspace"' in item["sql"]
        )
        self.assertNotIn(" JOIN ", workspace_lock_query)
        self.assertEqual(FranchiseResearchFinalization.objects.count(), 1)
        self.assertEqual(finalization.field_decisions.count(), 3)
        self.assertEqual(finalization.pending_count, 1)
        self.assertEqual(FranchiseResearchPublishedField.objects.count(), 2)
        publication = FranchiseResearchPublishedField.objects.get(
            target_field=accepted.target_field
        )
        self.assertEqual(publication.target_field, accepted.target_field)
        self.assertTrue(publication.is_current)
        imported.franchise.refresh_from_db()
        self.assertEqual(imported.franchise.name, "AI value")
        manual = FranchiseResearchEditorialDecision.objects.get(
            finalization=finalization,
            target_field="financials.private_contract_value",
        )
        self.assertEqual(manual.value_origin, "human")
        self.assertEqual(manual.effective_value, "250 000 PLN")
        self.assertIsNone(manual.research_field)
        self.assertEqual(manual.supporting_documents.count(), 1)
        manual_publication = FranchiseResearchPublishedField.objects.get(
            target_field="financials.private_contract_value"
        )
        self.assertEqual(
            manual_publication.franchise_attribute,
            "research.financials.private_contract_value",
        )
        self.assertEqual(
            manual_publication.status,
            FranchiseResearchPublishedField.STATUS_PROJECTED,
        )
        payload = json.loads(Path(finalization.artifact_reference).read_text())
        self.assertFalse(payload["privacy"]["document_bytes_included"])
        self.assertFalse(payload["privacy"]["storage_paths_included"])
        self.assertNotIn("TOP SECRET CONTRACT", Path(finalization.artifact_reference).read_text())
        self.assertEqual(accepted.decision, "accepted")
        response = self.client.get(
            reverse("franchises:research_detail", args=[imported.franchise.slug])
        )
        self.assertContains(response, "250 000 PLN")
        self.assertContains(response, "Dodatkowe pola redakcyjne")
        self.assertContains(response, "oczekuje na decyzję researchera")
        self.assertNotContains(response, pending_public_value)
        self.assertNotContains(response, "private-agreement.pdf")

    def test_paid_job_queue_builds_only_bounded_typed_command_and_records_failure(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="job-test",
            defaults={"name": "Job test"},
        )
        franchise = Franchise.objects.create(
            name="Job Brand",
            slug="job-brand",
            category=category,
            short_description="Job fixture",
        )
        workspace = FranchiseResearchWorkspace.objects.create(
            franchise=franchise,
            normalization_id=self.normalized.normalization_id,
            plan_run_id=self.normalized.plan_run_id,
            target_country=self.normalized.target_country,
            depth=self.normalized.depth.value,
            iteration=self.normalized.iteration,
            normalized_reference=str(self.lineage["normalized_path"]),
            normalized_sha256=self.lineage["normalized_sha256"],
        )
        check_path = Path(self.temporary_directory.name) / "check-current.json"
        check_path.write_text("checker fixture", encoding="utf-8")
        check_sha256 = hashlib.sha256(check_path.read_bytes()).hexdigest()
        configuration = {
            "policy": "advance",
            "max_cost_usd": "0.75",
            "max_rounds": 1,
            "normalize_incomplete": False,
            "max_search_calls": 8,
            "max_extractor_api_calls": 12,
        }
        with (
            patch(
                "franchises.research_jobs._current_check",
                return_value=(check_path, check_sha256),
            ),
            patch(
                "franchises.research_jobs.load_checker_results",
                return_value=(self.checker, check_sha256),
            ),
        ):
            job = queue_research_job(
                workspace,
                kind=FranchiseResearchJob.KIND_LOOP,
                configuration=configuration,
            )
            with self.assertRaises(ResearchJobError):
                queue_research_job(
                    workspace,
                    kind=FranchiseResearchJob.KIND_LOOP,
                    configuration=configuration,
                )
            command = build_research_command(job)

        self.assertIsInstance(command, list)
        self.assertIn("--max-cost-usd", command)
        self.assertIn("0.75", command)
        self.assertIn("--advance-with-documented-gaps", command)
        self.assertNotIn("--free", command)
        claimed = claim_next_job()
        self.assertEqual(claimed.pk, job.pk)
        with patch("franchises.research_jobs.build_research_command", return_value=command):
            process_research_job(
                claimed,
                runner=lambda _job, _command: ResearchCommandResult(1, "provider failed"),
            )
        job.refresh_from_db()
        self.assertEqual(job.status, FranchiseResearchJob.STATUS_FAILED)
        self.assertEqual(job.error_code, "ResearchJobError")
        self.assertTrue(workspace.events.filter(event_type="job_failed").exists())

    def test_successful_normalizer_job_records_exact_cost_and_result_workspace(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="job-success-test",
            defaults={"name": "Job success test"},
        )
        franchise = Franchise.objects.create(
            name="Successful Job Brand",
            slug="successful-job-brand",
            category=category,
            short_description="Successful job fixture",
        )
        workspace = FranchiseResearchWorkspace.objects.create(
            franchise=franchise,
            normalization_id=self.normalized.normalization_id,
            plan_run_id=self.normalized.plan_run_id,
            target_country=self.normalized.target_country,
            depth=self.normalized.depth.value,
            iteration=self.normalized.iteration,
            normalized_reference=str(self.lineage["normalized_path"]),
            normalized_sha256=self.lineage["normalized_sha256"],
        )
        job = FranchiseResearchJob.objects.create(
            workspace=workspace,
            kind=FranchiseResearchJob.KIND_NORMALIZE,
            status=FranchiseResearchJob.STATUS_RUNNING,
            input_reference=str(self.lineage["check_path"]),
            input_sha256=self.lineage["check_sha256"],
            configuration={"normalize_incomplete": True},
        )
        normalized_path = Path(self.temporary_directory.name) / "normalized-new.json"
        normalized_path.write_text("fixture", encoding="utf-8")
        output = json.dumps(
            {
                "normalized_path": str(normalized_path),
                "usage_totals": {
                    "api_attempts_recorded": 1,
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "reasoning_tokens": 5,
                    "total_tokens": 120,
                    "tool_calls": 0,
                    "tool_cost_usd": "0",
                    "estimated_cost_usd": "0.01250000",
                },
            }
        )
        with (
            patch(
                "franchises.research_jobs.build_research_command",
                return_value=["python", "-m", "datacollector", "normalize"],
            ),
            patch(
                "franchises.research_jobs.load_normalizer_results",
                return_value=(self.normalized, "d" * 64),
            ),
            patch(
                "franchises.research_jobs.create_research_workspace",
                return_value=(workspace, False),
            ),
        ):
            process_research_job(
                job,
                runner=lambda _job, _command: ResearchCommandResult(0, output),
            )
        job.refresh_from_db()
        self.assertEqual(job.status, FranchiseResearchJob.STATUS_SUCCEEDED)
        self.assertEqual(job.progress_percent, 100)
        self.assertEqual(job.cost_summary["estimated_cost_usd"], "0.01250000")
        self.assertEqual(job.result_workspace, workspace)
        self.assertEqual(job.result_normalized_sha256, "d" * 64)
        self.assertTrue(workspace.events.filter(event_type="job_succeeded").exists())

    def test_finalizer_job_is_durable_zero_cost_and_skips_subprocess(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="finalizer-job-test",
            defaults={"name": "Finalizer job test"},
        )
        franchise = Franchise.objects.create(
            name="Finalizer Job Brand",
            slug="finalizer-job-brand",
            category=category,
            short_description="Finalizer job fixture",
        )
        reviewer = get_user_model().objects.create_user(
            "job-finalizer", "job-finalizer@example.com", "DemoTest123!"
        )
        workspace = FranchiseResearchWorkspace.objects.create(
            franchise=franchise,
            normalization_id=self.normalized.normalization_id,
            plan_run_id=self.normalized.plan_run_id,
            target_country=self.normalized.target_country,
            depth=self.normalized.depth.value,
            iteration=self.normalized.iteration,
            normalized_reference=str(self.lineage["normalized_path"]),
            normalized_sha256=self.lineage["normalized_sha256"],
            status=FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS,
            reviewed_by=reviewer,
            reviewed_at=timezone.now(),
        )
        job = queue_research_job(
            workspace,
            kind=FranchiseResearchJob.KIND_FINALIZE,
            configuration={},
            requested_by=reviewer,
        )
        claimed = claim_next_job()
        finalization = type(
            "FinalizationResult",
            (),
            {
                "finalization_id": "b5f2766f-be59-58b7-86fa-7820df9cdf0e",
                "release_number": 2,
                "research_import_id": 17,
                "research_import": type(
                    "ImportResult",
                    (),
                    {"franchise": franchise},
                )(),
                "artifact_sha256": "a" * 64,
            },
        )()
        runner = patch("franchises.research_jobs._run_monitored_command")
        with (
            patch(
                "franchises.research_jobs.finalize_research_workspace",
                return_value=(finalization, True),
            ) as finalizer,
            runner as subprocess_runner,
        ):
            process_research_job(claimed)
        job.refresh_from_db()
        self.assertEqual(job.status, FranchiseResearchJob.STATUS_SUCCEEDED)
        self.assertEqual(job.result_summary["release_number"], 2)
        self.assertEqual(job.cost_summary["estimated_cost_usd"], "0")
        self.assertFalse(subprocess_runner.called)
        self.assertEqual(finalizer.call_args.kwargs["active_job_id"], job.job_id)

    def test_initial_launch_runs_typed_paid_stages_and_creates_workspace(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="launch-test",
            defaults={"name": "Launch test"},
        )
        franchise = Franchise.objects.create(
            name=self.plan.planner_input.brand_name,
            slug="launch-brand",
            category=category,
            short_description="Launch fixture",
        )
        launch = queue_research_launch(
            franchise,
            profile_id="PL:L1",
            known_legal_name="Fixture Operator sp. z o.o.",
            known_official_website="https://example.com/",
            configuration={
                "max_cost_usd": "99.00",
                "initial_task_limit": 5,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
            },
        )
        claimed = claim_next_launch()
        paths = {
            "plan": Path(self.temporary_directory.name) / "plan.json",
            "seed_sources": Path(self.temporary_directory.name) / "sources-free.json",
            "seed_extractions": Path(self.temporary_directory.name) / "extractions.json",
            "check": Path(self.temporary_directory.name) / "check.json",
            "normalized": Path(self.temporary_directory.name) / "normalized-launch.json",
        }
        for path in paths.values():
            path.write_text("fixture", encoding="utf-8")
        summaries = [
            {"plan_path": str(paths["plan"]), "agent_usage": [{"input_tokens": 10, "output_tokens": 2, "reasoning_tokens": 0, "total_tokens": 12, "tool_calls": 0, "tool_cost_usd": "0", "estimated_cost_usd": "0.01"}]},
            {"sources_path": str(paths["seed_sources"]), "usage_totals": {"api_attempts_recorded": 0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0, "tool_calls": 0, "tool_cost_usd": "0", "estimated_cost_usd": "0"}},
            {"extractions_path": str(paths["seed_extractions"]), "usage_totals": {"api_attempts_recorded": 1, "input_tokens": 10, "output_tokens": 2, "reasoning_tokens": 0, "total_tokens": 12, "tool_calls": 0, "tool_cost_usd": "0", "estimated_cost_usd": "0.02"}},
            {"check_path": str(paths["check"]), "usage_totals": {"api_attempts_recorded": 1, "input_tokens": 10, "output_tokens": 2, "reasoning_tokens": 0, "total_tokens": 12, "tool_calls": 0, "tool_cost_usd": "0", "estimated_cost_usd": "0.02"}},
            {"normalized_path": str(paths["normalized"]), "usage_totals": {"api_attempts_recorded": 1, "input_tokens": 10, "output_tokens": 2, "reasoning_tokens": 0, "total_tokens": 12, "tool_calls": 0, "tool_cost_usd": "0", "estimated_cost_usd": "0.02"}},
        ]
        commands = []

        def runner(_launch, command):
            commands.append(command)
            return ResearchCommandResult(0, json.dumps(summaries[len(commands) - 1]))

        with (
            patch("franchises.research_launches.load_research_plan", return_value=(self.plan, self.search.plan_sha256)),
            patch("franchises.research_launches.load_search_results", return_value=(self.search, self.extraction.search_sha256)),
            patch("franchises.research_launches.load_extraction_results", return_value=(self.extraction, self.checker.extraction_sha256)),
            patch("franchises.research_launches.load_checker_results", return_value=(self.checker, self.normalized.check_sha256)),
            patch("franchises.research_launches.load_normalizer_results", return_value=(self.normalized, "e" * 64)),
            patch(
                "franchises.research_launches._accepted_checker_fields",
                return_value={f"field-{index}" for index in range(8)},
            ),
            patch("franchises.research_launches.create_research_workspace") as create_workspace,
        ):
            workspace = FranchiseResearchWorkspace.objects.create(
                franchise=franchise,
                normalization_id=self.normalized.normalization_id,
                plan_run_id=self.normalized.plan_run_id,
                target_country="PL",
                depth=self.normalized.depth.value,
                iteration=1,
                normalized_reference=str(paths["normalized"]),
                normalized_sha256="e" * 64,
            )
            create_workspace.return_value = (workspace, True)
            process_research_launch(claimed, runner=runner)
        launch.refresh_from_db()
        self.assertEqual(
            launch.status,
            FranchiseResearchLaunch.STATUS_INSUFFICIENT,
        )
        self.assertEqual(launch.result_workspace, workspace)
        self.assertEqual(len(commands), 5)
        self.assertIn("--profile", commands[0])
        self.assertIn("PL:L1", commands[0])
        self.assertIn("--offline", commands[0])
        self.assertIn("--official-first", commands[1])
        self.assertNotIn("--offline", commands[2])
        self.assertIn("--document-cache-dir", commands[2])
        self.assertIn("--risk-based", commands[3])
        self.assertNotIn("search", commands[4])
        self.assertTrue(launch.result_summary["paid_search_skipped"])
        self.assertEqual(launch.result_summary["l1_route"], "official_only")
        self.assertEqual(launch.cost_summary["estimated_cost_usd"], "0.07")

    def test_hybrid_l1_routes_only_checker_gaps_through_resolver_executor(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="hybrid-gap-test",
            defaults={"name": "Hybrid gap test"},
        )
        franchise = Franchise.objects.create(
            name=self.plan.planner_input.brand_name,
            slug="hybrid-gap-brand",
            category=category,
            short_description="Hybrid gap fixture",
        )
        launch = queue_research_launch(
            franchise,
            profile_id="PL:L1",
            known_legal_name="",
            known_official_website="https://example.com/",
            configuration={
                "max_cost_usd": "99.00",
                "initial_task_limit": 5,
                "max_search_calls": 8,
                "max_sources": 9,
                "max_extractor_api_calls": 12,
            },
        )
        claimed = claim_next_launch()
        claimed.plan_sha256 = self.search.plan_sha256
        claimed.save(update_fields=["plan_sha256"])
        root = Path(self.temporary_directory.name)
        paths = {
            name: root / filename
            for name, filename in {
                "plan": "plan-hybrid.json",
                "seed_sources": "sources-r001-free.json",
                "seed_extractions": "extractions-r001.json",
                "seed_check": "check-r001.json",
                "resolution": "resolution-r001-free.json",
                "execution": "execution-r002.json",
                "sources": "sources-r002.json",
                "extractions": "extractions-r002.json",
            }.items()
        }
        for path in paths.values():
            path.write_text("fixture", encoding="utf-8")
        zero = {
            "api_attempts_recorded": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
            "tool_calls": 0,
            "tool_cost_usd": "0",
            "estimated_cost_usd": "0",
        }
        summaries = [
            {"sources_path": str(paths["seed_sources"]), "usage_totals": zero},
            {"extractions_path": str(paths["seed_extractions"]), "usage_totals": zero},
            {"check_path": str(paths["seed_check"]), "usage_totals": zero},
            {"resolution_path": str(paths["resolution"]), "usage_totals": zero},
            {
                "execution_path": str(paths["execution"]),
                "sources_path": str(paths["sources"]),
                "extractions_path": str(paths["extractions"]),
                "usage_totals": zero,
            },
        ]
        commands = []

        def runner(_launch, command):
            commands.append(command)
            return ResearchCommandResult(0, json.dumps(summaries[len(commands) - 1]))

        with (
            patch(
                "franchises.research_launches.load_search_results",
                return_value=(self.search, self.extraction.search_sha256),
            ),
            patch(
                "franchises.research_launches.load_extraction_results",
                return_value=(self.extraction, self.checker.extraction_sha256),
            ),
            patch(
                "franchises.research_launches.load_checker_results",
                return_value=(self.checker, self.normalized.check_sha256),
            ),
            patch(
                "franchises.research_launches._accepted_checker_fields",
                return_value={"brand.name"},
            ),
        ):
            sources_path, extractions_path, check_path, summary = (
                _process_hybrid_l1_discovery(
                    claimed,
                    runner,
                    python=".venv/bin/python",
                    plan=self.plan,
                    plan_path=paths["plan"],
                    config=claimed.configuration,
                    summaries=[],
                )
            )
        self.assertEqual(sources_path, paths["sources"].resolve())
        self.assertEqual(extractions_path, paths["extractions"].resolve())
        self.assertIsNone(check_path)
        self.assertEqual(summary["route"], "official_plus_gap_search")
        self.assertEqual(len(commands), 5)
        self.assertIn("--offline", commands[0])
        self.assertNotIn("--offline", commands[1])
        self.assertIn("--risk-based", commands[2])
        self.assertIn("resolve", commands[3])
        self.assertIn("--free", commands[3])
        self.assertIn("execute", commands[4])
        self.assertIn("--document-cache-dir", commands[4])

    def test_campaign_claim_respects_per_campaign_concurrency(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="campaign-test",
            defaults={"name": "Campaign test"},
        )
        franchises = [
            Franchise.objects.create(
                name=f"Campaign Brand {index}",
                slug=f"campaign-brand-{index}",
                category=category,
                short_description="Campaign fixture",
            )
            for index in range(2)
        ]
        campaign = create_research_campaign(
            name="Concurrency test",
            description="",
            franchises=franchises,
            profile_id="PL:L1",
            configuration={
                "max_cost_usd": "1.00",
                "initial_task_limit": 5,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
            },
            max_total_cost_usd="2.00",
            max_concurrent_runs=1,
            include_previously_researched=False,
        )
        first = claim_next_launch()
        self.assertEqual(first.campaign, campaign)
        self.assertIsNone(claim_next_launch())
        first.status = FranchiseResearchLaunch.STATUS_SUCCEEDED
        first.progress_percent = 100
        first.completed_at = timezone.now()
        first.save(update_fields=["status", "progress_percent", "completed_at"])
        sync_campaign(campaign)
        second = claim_next_launch()
        self.assertIsNotNone(second)
        self.assertNotEqual(second.pk, first.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, FranchiseResearchCampaign.STATUS_RUNNING)

    def test_campaign_cancel_keeps_running_launch_and_cancels_queue(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="campaign-cancel-test",
            defaults={"name": "Campaign cancel test"},
        )
        franchises = [
            Franchise.objects.create(
                name=f"Cancel Brand {index}",
                slug=f"cancel-brand-{index}",
                category=category,
                short_description="Campaign fixture",
            )
            for index in range(2)
        ]
        campaign = create_research_campaign(
            name="Cancellation test",
            description="",
            franchises=franchises,
            profile_id="PL:L1",
            configuration={
                "max_cost_usd": "1.00",
                "initial_task_limit": 5,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
            },
            max_total_cost_usd="2.00",
            max_concurrent_runs=1,
            include_previously_researched=False,
        )
        running = claim_next_launch()
        self.assertEqual(cancel_research_campaign(campaign), 1)
        running.refresh_from_db()
        campaign.refresh_from_db()
        self.assertEqual(running.status, FranchiseResearchLaunch.STATUS_RUNNING)
        self.assertTrue(campaign.cancel_requested)
        self.assertEqual(campaign.status, FranchiseResearchCampaign.STATUS_RUNNING)
        self.assertEqual(
            campaign.launches.filter(status=FranchiseResearchLaunch.STATUS_CANCELLED).count(),
            1,
        )
        running.status = FranchiseResearchLaunch.STATUS_SUCCEEDED
        running.completed_at = timezone.now()
        running.save(update_fields=["status", "completed_at"])
        sync_campaign(campaign)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, FranchiseResearchCampaign.STATUS_CANCELLED)

    def test_campaign_monitoring_gate_stops_queue_after_minimum_sample(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="campaign-gate-test",
            defaults={"name": "Campaign gate test"},
        )
        franchises = [
            Franchise.objects.create(
                name=f"Gate Brand {index}",
                slug=f"gate-brand-{index}",
                category=category,
                short_description="Campaign gate fixture",
            )
            for index in range(4)
        ]
        campaign = create_research_campaign(
            name="Quality gate test",
            description="",
            franchises=franchises,
            profile_id="PL:L1",
            configuration={
                "max_cost_usd": "1.00",
                "initial_task_limit": 7,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
                "auto_review_finalize": True,
                "monitoring_gate": {
                    "enabled": True,
                    "minimum_completed": 3,
                    "minimum_average_proposals": "8",
                    "minimum_average_publications": "5",
                    "stop_on_unknown_cost": True,
                },
            },
            max_total_cost_usd="4.00",
            max_concurrent_runs=1,
            include_previously_researched=False,
        )

        for position in range(1, 4):
            launch = campaign.launches.get(campaign_position=position)
            launch.status = FranchiseResearchLaunch.STATUS_PARTIAL
            launch.progress_percent = 100
            launch.result_summary = {
                "proposed_fields": 7,
                "auto_finalization": {"published_fields": 4},
            }
            launch.cost_summary = {
                "estimated_cost_usd": "0.40",
                "unknown_cost_attempts": 0,
            }
            launch.completed_at = timezone.now()
            launch.save()
            sync_campaign(campaign)
            campaign.refresh_from_db()
            if position < 3:
                self.assertFalse(campaign.cancel_requested)

        campaign.refresh_from_db()
        state = campaign.configuration["monitoring_gate_state"]
        self.assertTrue(campaign.cancel_requested)
        self.assertEqual(
            state["triggered_stop_codes"],
            [
                "average_proposals_below_gate",
                "average_publications_below_gate",
            ],
        )
        self.assertEqual(
            campaign.launches.filter(
                status=FranchiseResearchLaunch.STATUS_CANCELLED
            ).count(),
            1,
        )
        self.assertEqual(
            campaign.status,
            FranchiseResearchCampaign.STATUS_CANCELLED,
        )

    def test_launch_reserves_budget_instead_of_hiding_known_cost(self):
        usage = _combined_usage(
            [
                {
                    "agent_usage": [
                        {
                            "tokens": {
                                "input_tokens": 1000,
                                "output_tokens": 100,
                                "reasoning_tokens": 10,
                                "total_tokens": 1100,
                            },
                            "cost_estimate": {
                                "total_estimated_cost_usd": "0.10",
                                "tool_cost_usd": "0",
                            },
                        }
                    ],
                    "failed_attempts": [
                        {
                            "error_code": "provider_exception",
                            "token_usage_unknown": True,
                        }
                    ],
                }
            ]
        )
        self.assertEqual(usage["estimated_cost_usd"], "0.10")
        self.assertFalse(usage["cost_complete"])
        self.assertEqual(usage["unknown_cost_attempts"], 1)
        self.assertEqual(usage["unknown_cost_reserve_usd"], "0.50")
        self.assertEqual(usage["budgeted_cost_usd"], "0.60")

    @override_settings(
        RESEARCH_TRANSIENT_STAGE_RETRIES=2,
        RESEARCH_TRANSIENT_RETRY_DELAY_SECONDS=0,
    )
    def test_launch_retries_transient_provider_failure_and_reserves_unknown_cost(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="transient-retry-test",
            defaults={"name": "Transient retry test"},
        )
        franchise = Franchise.objects.create(
            name="Transient Retry Brand",
            slug="transient-retry-brand",
            category=category,
            short_description="Retry fixture",
        )
        launch = queue_research_launch(
            franchise,
            profile_id="PL:L1",
            known_legal_name="",
            known_official_website="",
            configuration={
                "max_cost_usd": "1.00",
                "initial_task_limit": 5,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
            },
        )
        claimed = claim_next_launch()
        successful_summary = {
            "sources_path": "/tmp/sources.json",
            "usage_totals": {
                "api_attempts_recorded": 1,
                "input_tokens": 100,
                "output_tokens": 20,
                "reasoning_tokens": 0,
                "total_tokens": 120,
                "tool_calls": 1,
                "tool_cost_usd": "0.01",
                "estimated_cost_usd": "0.02",
            },
        }
        calls = []

        def runner(_launch, _command):
            calls.append(1)
            if len(calls) == 1:
                return ResearchCommandResult(
                    2,
                    "error: OpenAI Searcher request failed (InternalServerError).",
                )
            return ResearchCommandResult(0, json.dumps(successful_summary))

        summaries = []
        result = _run_stage(
            claimed,
            runner,
            ["search"],
            label="Searcher / wyszukiwanie źródeł",
            progress=25,
            summaries=summaries,
        )
        claimed.refresh_from_db()
        self.assertEqual(result, successful_summary)
        self.assertEqual(len(calls), 2)
        self.assertEqual(claimed.cost_summary["unknown_cost_attempts"], 1)
        self.assertFalse(claimed.cost_summary["cost_complete"])
        self.assertEqual(claimed.cost_summary["estimated_cost_usd"], "0.02")
        self.assertEqual(claimed.cost_summary["budgeted_cost_usd"], "0.52")
        self.assertEqual(len(claimed.provider_failure_history), 1)
        self.assertEqual(
            claimed.provider_failure_history[0]["error_code"],
            "provider_server_error",
        )
        self.assertIn("automatyczne ponowienie 1/2", claimed.log)

    @override_settings(
        RESEARCH_TRANSIENT_STAGE_RETRIES=2,
        RESEARCH_TRANSIENT_RETRY_DELAY_SECONDS=0,
    )
    def test_launch_does_not_retry_validation_failure(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="non-retry-test",
            defaults={"name": "Non retry test"},
        )
        franchise = Franchise.objects.create(
            name="Non Retry Brand",
            slug="non-retry-brand",
            category=category,
            short_description="Retry fixture",
        )
        launch = queue_research_launch(
            franchise,
            profile_id="PL:L1",
            known_legal_name="",
            known_official_website="",
            configuration={
                "max_cost_usd": "1.00",
                "initial_task_limit": 5,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
            },
        )
        claimed = claim_next_launch()
        calls = []

        def runner(_launch, _command):
            calls.append(1)
            return ResearchCommandResult(2, "error: invalid CLI argument")

        with self.assertRaises(ResearchLaunchError):
            _run_stage(
                claimed,
                runner,
                ["search"],
                label="Searcher / wyszukiwanie źródeł",
                progress=25,
                summaries=[],
            )
        self.assertEqual(len(calls), 1)

    def test_launch_records_known_usage_from_paid_failure_ledger(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="known-failure-ledger-test",
            defaults={"name": "Known failure ledger test"},
        )
        franchise = Franchise.objects.create(
            name="Known Failure Brand",
            slug="known-failure-brand",
            category=category,
            short_description="Failure ledger fixture",
        )
        launch = queue_research_launch(
            franchise,
            profile_id="PL:L1",
            known_legal_name="",
            known_official_website="",
            configuration={
                "max_cost_usd": "1.00",
                "initial_task_limit": 5,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
            },
        )
        claimed = claim_next_launch()
        root = Path(self.temporary_directory.name)
        attempts = root / "attempts"
        attempts.mkdir()
        ledger_path = attempts / "searcher-r001-c001-failed-fixture.json"
        ledger_path.write_text(
            json.dumps(
                {
                    "error_code": "postprocessing_error",
                    "usage": {
                        "agent": "searcher",
                        "iteration": 1,
                        "call_index": 1,
                        "tokens": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "reasoning_tokens": 5,
                            "total_tokens": 120,
                        },
                        "tool_usage": [{"tool": "web_search", "calls": 1}],
                        "cost_estimate": {
                            "tool_cost_usd": "0.01",
                            "total_estimated_cost_usd": "0.25",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        with (
            patch("franchises.research_launches.REPOSITORY_ROOT", root),
            self.assertRaises(ResearchLaunchError),
        ):
            _run_stage(
                claimed,
                lambda _launch, _command: ResearchCommandResult(
                    2,
                    "error: post-processing failed. "
                    f"Provider usage saved to: {ledger_path}.",
                ),
                ["search"],
                label="Searcher / wyszukiwanie źródeł",
                progress=25,
                summaries=[],
            )

        claimed.refresh_from_db()
        self.assertEqual(claimed.cost_summary["api_attempts_recorded"], 1)
        self.assertEqual(claimed.cost_summary["estimated_cost_usd"], "0.25")
        self.assertEqual(claimed.cost_summary["unknown_cost_attempts"], 0)
        self.assertTrue(claimed.cost_summary["cost_complete"])
        self.assertEqual(len(claimed.provider_failure_history), 1)
        self.assertTrue(claimed.provider_failure_history[0]["usage_recorded"])

    def test_launch_recovers_paid_search_artifact_from_log(self):
        category, _ = FranchiseCategory.objects.get_or_create(
            slug="recover-search-test",
            defaults={"name": "Recover search test"},
        )
        franchise = Franchise.objects.create(
            name="Recover Search Brand",
            slug="recover-search-brand",
            category=category,
            short_description="Recover fixture",
        )
        launch = FranchiseResearchLaunch.objects.create(
            franchise=franchise,
            profile_id="PL:L1",
            status=FranchiseResearchLaunch.STATUS_RUNNING,
        )
        root = Path(self.temporary_directory.name)
        sources_path = root / "sources.json"
        sources_path.write_text("{}", encoding="utf-8")
        launch.log = json.dumps({"sources_path": str(sources_path)})
        launch.save(update_fields=["log"])

        with (
            patch("franchises.research_launches.REPOSITORY_ROOT", root),
            patch(
                "franchises.research_launches.load_search_results",
                return_value=(self.search, "a" * 64),
            ),
        ):
            recovered = _recover_paid_search_artifact(
                launch,
                plan_sha256=self.search.plan_sha256,
            )

        launch.refresh_from_db()
        self.assertEqual(recovered, sources_path)
        self.assertEqual(launch.sources_reference, str(sources_path))
        self.assertEqual(launch.sources_sha256, "a" * 64)
        self.assertIn("zapytanie nie będzie ponawiane", launch.log)
