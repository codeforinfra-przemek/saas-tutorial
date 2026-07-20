from datetime import datetime, timezone
from unittest import TestCase
from uuid import uuid4

from datacollector.loop import (
    LOOP_RUN_SCHEMA_VERSION,
    LOOP_SEQUENCE,
    LoopAgent,
    LoopPolicy,
    LoopRunResults,
)


class LoopBlueprintTests(TestCase):
    @staticmethod
    def run_payload(**updates):
        now = datetime.now(timezone.utc)
        payload = {
            "schema_version": LOOP_RUN_SCHEMA_VERSION,
            "loop_id": str(uuid4()),
            "plan_run_id": str(uuid4()),
            "plan_reference": "/tmp/plan.json",
            "plan_sha256": "a" * 64,
            "brand_name": "Example",
            "started_at": now,
            "completed_at": now,
            "initial_check_id": str(uuid4()),
            "initial_check_reference": "/tmp/check-before.json",
            "initial_check_sha256": "b" * 64,
            "final_check_id": str(uuid4()),
            "final_check_reference": "/tmp/check-after.json",
            "final_check_sha256": "c" * 64,
            "policy": LoopPolicy(),
            "rounds": [],
            "post_loop_usage": [],
            "stop_reason": "max_rounds",
            "final_quality_score": 0,
            "final_quality_threshold": 80,
            "final_scope_complete": False,
            "final_checker_passed": False,
            "incremental_api_attempts": 0,
            "incremental_input_tokens": 0,
            "incremental_output_tokens": 0,
            "incremental_reasoning_tokens": 0,
            "incremental_total_tokens": 0,
            "incremental_tool_calls": 0,
            "incremental_tool_cost_usd": "0",
            "incremental_estimated_cost_usd": "0",
            "normalization_reference": None,
            "recommended_next_action": "resume_loop",
            "warnings": [],
        }
        payload.update(updates)
        return payload

    def test_blueprint_begins_with_planner_and_requires_human_gate(self):
        policy = LoopPolicy()

        self.assertEqual(LOOP_SEQUENCE[0], LoopAgent.PLANNER)
        self.assertLess(
            LOOP_SEQUENCE.index(LoopAgent.HUMAN_REVIEW),
            LOOP_SEQUENCE.index(LoopAgent.IMPORTER),
        )
        self.assertTrue(policy.require_human_review_before_import)
        self.assertFalse(policy.publish_automatically)

    def test_exhausted_scope_choices_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValueError, "cannot both continue gap repair"):
            LoopPolicy(
                allow_plan_repair_limit=True,
                advance_with_documented_gaps=True,
            )

    def test_schema_1_0_loop_artifact_remains_loadable_without_profile(self):
        result = LoopRunResults.model_validate(
            self.run_payload(schema_version="1.0.0")
        )

        self.assertIsNone(result.profile_id)
        self.assertIsNone(result.profile_sha256)
        self.assertIsNone(result.research_level)

    def test_profile_metadata_is_complete_and_versioned(self):
        result = LoopRunResults.model_validate(
            self.run_payload(
                profile_id="PL:L2:v1",
                profile_sha256="d" * 64,
                research_level="L2",
            )
        )

        self.assertEqual(result.schema_version, "1.1.0")
        self.assertEqual(result.profile_id, "PL:L2:v1")
        self.assertEqual(result.research_level, "L2")

        with self.assertRaisesRegex(ValueError, "either complete or absent"):
            LoopRunResults.model_validate(
                self.run_payload(profile_id="PL:L2:v1")
            )
