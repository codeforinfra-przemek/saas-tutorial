from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .research_import import import_franchise_research

from datacollector.agents.reviewer import HumanReviewer
from datacollector.schemas import HumanReviewDecision, NormalizerMode
from datacollector.tests import test_normalizer as normalizer_fixtures

from .models import (
    FranchiseResearchArtifact,
    FranchiseResearchCitation,
    FranchiseResearchClaim,
    FranchiseResearchField,
    FranchiseResearchImport,
    FranchiseResearchSource,
    FranchiseResearchTask,
    FranchiseResearchValue,
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
