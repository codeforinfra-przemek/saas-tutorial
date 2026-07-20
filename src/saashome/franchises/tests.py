import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .research_import import _resolved, import_franchise_research
from .research_workbench import create_research_workspace
from .research_jobs import (
    ResearchCommandResult,
    ResearchJobError,
    build_research_command,
    claim_next_job,
    process_research_job,
    queue_research_job,
)

from datacollector.agents.reviewer import HumanReviewer
from datacollector.schemas import HumanReviewDecision, NormalizerMode
from datacollector.tests import test_normalizer as normalizer_fixtures

from .models import (
    Franchise,
    FranchiseCategory,
    FranchiseResearchArtifact,
    FranchiseResearchCitation,
    FranchiseResearchClaim,
    FranchiseResearchField,
    FranchiseResearchImport,
    FranchiseResearchJob,
    FranchiseResearchSource,
    FranchiseResearchTask,
    FranchiseResearchValue,
    FranchiseResearchWorkspace,
)


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
        self.assertEqual(imported.franchise.data_status, "research_reviewed")
        self.assertTrue(imported.franchise.is_verified)

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

    def test_super_detailed_view_shows_values_gaps_and_evidence(self):
        with patch(
            "franchises.research_import._load_approved_lineage",
            return_value=self.lineage,
        ):
            imported, _ = import_franchise_research(self.review_path)

        response = self.client.get(
            reverse("franchises:research_detail", args=[imported.franchise.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pełny zakres planu")
        self.assertContains(response, "brand.name")
        self.assertContains(response, "Rejestr źródeł")
        self.assertContains(response, self.extraction.citations[0].quote)

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
