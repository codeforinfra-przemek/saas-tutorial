from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .research_import import _resolved, import_franchise_research
from .research_workbench import create_research_workspace

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
