from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from datacollector.agents.reviewer import (
    HumanReviewer,
    HumanReviewValidationError,
    render_review_html,
)
from datacollector.schemas import HumanReviewDecision, NormalizerMode
from datacollector.storage.json_store import (
    load_human_review_results,
    save_human_review_results,
)
from datacollector.tests import test_normalizer as normalizer_fixtures


class HumanReviewerTests(TestCase):
    @classmethod
    def setUpClass(cls):
        normalizer_fixtures.NormalizerAgentTests.setUpClass()
        cls.fixture = normalizer_fixtures.NormalizerAgentTests(
            "test_free_normalizer_preserves_every_accepted_claim_and_provenance"
        )
        cls.plan = normalizer_fixtures.NormalizerAgentTests.plan
        cls.search = normalizer_fixtures.NormalizerAgentTests.search_results
        cls.extraction = normalizer_fixtures.NormalizerAgentTests.extraction_results
        cls.checker = normalizer_fixtures.NormalizerAgentTests.checker_results
        cls.normalized = cls.fixture._run(mode=NormalizerMode.FREE)
        cls.paid_normalized = cls.fixture._run(
            normalizer_fixtures.FixtureNormalizerLLM(),
            mode=NormalizerMode.PAID,
        )

    def _create(self, directory, **kwargs):
        normalized_path = Path(directory) / "normalized.json"
        normalized_path.write_text("fixture", encoding="utf-8")
        report_path = Path(directory) / "review-r004-pending.html"
        return HumanReviewer().create_review(
            self.plan,
            self.search,
            self.extraction,
            self.checker,
            kwargs.pop("normalized", self.normalized),
            plan_sha256=normalizer_fixtures.checker_fixtures.PLAN_SHA256,
            search_sha256=normalizer_fixtures.checker_fixtures.SEARCH_SHA256,
            extraction_sha256=normalizer_fixtures.checker_fixtures.EXTRACTION_SHA256,
            check_sha256=normalizer_fixtures.CHECK_SHA256,
            normalized_sha256="e" * 64,
            normalized_reference=str(normalized_path),
            report_reference=str(report_path),
            **kwargs,
        )

    def test_pending_review_reports_complete_scope_and_escapes_html(self):
        with TemporaryDirectory() as directory:
            review = self._create(directory, reviewer_notes="<script>alert(1)</script>")
            rendered = render_review_html(
                review,
                self.plan,
                self.search,
                self.extraction,
                self.checker,
                self.normalized,
            )

            self.assertEqual(review.decision, HumanReviewDecision.PENDING)
            self.assertEqual(review.coverage.planned_tasks, 1)
            self.assertEqual(review.coverage.planned_fields, 7)
            self.assertIn("brand.name", rendered)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
            self.assertNotIn("<script>alert(1)</script>", rendered)

    def test_incomplete_approval_requires_explicit_with_gaps_decision(self):
        incomplete = self.paid_normalized.model_copy(
            update={
                "input_checker_passed": False,
                "input_scope_complete": False,
                "incomplete_input_allowed": True,
            }
        )
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(HumanReviewValidationError, "approved_with_gaps"):
                self._create(
                    directory,
                    normalized=incomplete,
                    decision=HumanReviewDecision.APPROVED,
                    reviewer="Reviewer",
                )
            review = self._create(
                directory,
                normalized=incomplete,
                decision=HumanReviewDecision.APPROVED_WITH_GAPS,
                reviewer="Reviewer",
                acknowledge_incomplete=True,
            )

            self.assertTrue(review.approved_for_import)
            self.assertTrue(review.incomplete_input_acknowledged)

    def test_review_json_and_html_are_immutable_and_loadable(self):
        with TemporaryDirectory() as directory:
            review = self._create(directory)
            report = render_review_html(
                review,
                self.plan,
                self.search,
                self.extraction,
                self.checker,
                self.normalized,
            )
            review_path, report_path = save_human_review_results(
                review,
                report,
                Path(directory) / "normalized.json",
            )
            loaded, digest = load_human_review_results(review_path)

            self.assertEqual(loaded.review_id, review.review_id)
            self.assertEqual(len(digest), 64)
            self.assertEqual(report_path.read_text(encoding="utf-8"), report)
            with self.assertRaises(FileExistsError):
                save_human_review_results(
                    review,
                    report,
                    Path(directory) / "normalized.json",
                )
