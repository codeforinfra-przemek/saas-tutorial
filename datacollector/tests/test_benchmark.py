import json
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from contextlib import redirect_stdout

from datacollector.benchmark import (
    create_gold_set,
    create_submission,
    evaluate_submission,
    field_policy_map,
    load_benchmark_spec,
)
from datacollector.cli import main


class L1BenchmarkTests(TestCase):
    def setUp(self):
        self.spec = load_benchmark_spec()

    def test_spec_freezes_ten_brands_and_twenty_decision_fields(self):
        self.assertEqual(self.spec.profile_id, "PL:L1:v2")
        self.assertEqual(len(self.spec.brands), 10)
        self.assertEqual(len(self.spec.fields), 20)
        self.assertEqual(
            len({field.target_field for field in self.spec.fields}),
            20,
        )

    def test_gold_set_is_independent_blank_scaffold(self):
        gold = create_gold_set(self.spec, researcher="Independent researcher")

        self.assertEqual(len(gold.brands), 10)
        self.assertTrue(
            all(len(brand.fields) == 20 for brand in gold.brands)
        )
        self.assertTrue(
            all(
                field.status == "pending"
                for brand in gold.brands
                for field in brand.fields
            )
        )
        self.assertIn("independently", " ".join(gold.instructions))
        self.assertEqual(gold.methodology, "unspecified")

    def test_field_policy_only_applies_to_exact_l1_release(self):
        policies = field_policy_map("PL:L1:v2")

        self.assertEqual(policies["websites.official"].freshness_mode, "active_source")
        self.assertEqual(
            policies["investment.total_low"].freshness_mode,
            "explicit_as_of",
        )
        self.assertEqual(field_policy_map("PL:L1:v1"), {})

    def test_evaluator_distinguishes_proposals_from_publishable_acceptance(self):
        submission = create_submission(self.spec, method="pipeline", operator="QA")
        for brand in submission.brands:
            brand.tasks_attempted = brand.tasks_total
            brand.review_minutes = 10
            brand.known_cost_usd = Decimal("1")
            for field in brand.fields[:12]:
                field.proposal_status = "proposed"
                field.proposed_value = f"value for {field.target_field}"
                field.review_decision = "accepted_unchanged"
                field.source_url = "https://example.com/source"
                field.source_type = "official"
                field.observed_at = date(2026, 7, 21)
                field.valid_as_of = date(2026, 7, 21)
            for field in brand.fields[12:]:
                field.proposal_status = "gap"
                field.review_decision = "gap"

        result = evaluate_submission(self.spec, submission)

        self.assertTrue(result["passed"])
        self.assertEqual(result["aggregate"]["proposed_fields"], 120)
        self.assertEqual(result["aggregate"]["accepted_fields"], 120)
        self.assertEqual(result["aggregate"]["accepted_fields_per_usd"], 12)

        submission.brands[0].fields[0].review_decision = "rejected"
        changed = evaluate_submission(self.spec, submission)
        self.assertEqual(changed["brands"][0]["metrics"]["proposed_fields"], 12)
        self.assertEqual(changed["brands"][0]["metrics"]["accepted_fields"], 11)

    def test_unknown_cost_cannot_pass_efficiency_gate(self):
        submission = create_submission(self.spec, method="researcher_chatgpt")
        result = evaluate_submission(self.spec, submission)

        self.assertIsNone(
            result["brands"][0]["metrics"]["accepted_fields_per_usd"]
        )
        self.assertFalse(
            result["brands"][0]["gates"]["accepted_fields_per_usd"]
        )
        self.assertEqual(result["evaluation_status"], "not_ready")
        self.assertIn(
            "fields_not_assessed",
            result["brands"][0]["readiness_issues"],
        )
        self.assertEqual(result["readiness_issue_counts"]["fields_not_assessed"], 10)
        self.assertIn(
            "Run or export the exact benchmark campaign",
            result["next_actions"][0],
        )

    def test_zero_review_time_fails_but_absent_numeric_proposals_do_not(self):
        submission = create_submission(self.spec, method="pipeline")
        for brand in submission.brands:
            brand.tasks_attempted = brand.tasks_total
            brand.known_cost_usd = Decimal("1")
            brand.review_minutes = 0
            for field in brand.fields:
                field.proposal_status = "gap"
                field.review_decision = "gap"

        result = evaluate_submission(self.spec, submission)

        self.assertFalse(result["brands"][0]["gates"]["review_minutes"])
        self.assertTrue(
            result["brands"][0]["gates"]["public_numeric_metadata_rate"]
        )
        self.assertEqual(
            result["brands"][0]["metrics"]["public_numeric_metadata_rate"],
            1.0,
        )
        self.assertEqual(result["aggregate"]["public_numeric_metadata_rate"], 1.0)
        self.assertEqual(
            result["aggregate"]["review_measurement_methods"],
            ["not_recorded"],
        )

    def test_cli_creates_a_valid_submission_template(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "pipeline.json"
            stdout = StringIO()
            with redirect_stdout(stdout):
                exit_code = main(
                    [
                        "benchmark",
                        "--init-submission",
                        str(path),
                        "--method",
                        "pipeline",
                    ]
                )

            summary = json.loads(stdout.getvalue())
            artifact = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["brands"], 10)
            self.assertEqual(artifact["method"], "pipeline")
            self.assertEqual(len(artifact["brands"][0]["fields"]), 20)

            self.assertEqual(
                main(
                    [
                        "benchmark",
                        "--init-submission",
                        str(path),
                        "--method",
                        "pipeline",
                    ]
                ),
                2,
            )
