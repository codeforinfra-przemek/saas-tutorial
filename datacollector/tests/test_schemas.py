from unittest import TestCase

from pydantic import ValidationError

from datacollector.agents.planner import PlannerAgent
from datacollector.catalog import load_question_catalog
from datacollector.schemas import (
    AgentIterationUsage,
    PlannerInput,
    ResearchPlan,
    TokenUsage,
)


class PlannerInputTests(TestCase):
    def test_country_code_is_trimmed_and_normalized_before_validation(self):
        planner_input = PlannerInput(brand_name="Example", target_country=" us ")

        self.assertEqual(planner_input.target_country, "US")

    def test_country_code_must_be_two_ascii_letters(self):
        for invalid_code in ("1!", "USA", "PŁ", ""):
            with self.subTest(code=invalid_code):
                with self.assertRaises(ValidationError):
                    PlannerInput(brand_name="Example", target_country=invalid_code)


class ResearchPlanContractTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = PlannerAgent(load_question_catalog()).create_plan(
            PlannerInput(brand_name="Example")
        )

    def validate_mutated_plan(self, mutate):
        payload = self.plan.model_dump(mode="json")
        mutate(payload)
        return ResearchPlan.model_validate(payload)

    def test_run_id_must_be_uuid4(self):
        with self.assertRaisesRegex(ValidationError, "UUIDv4"):
            self.validate_mutated_plan(lambda payload: payload.update(run_id="not-a-uuid"))

    def test_offline_plan_cannot_declare_model(self):
        with self.assertRaisesRegex(ValidationError, "Offline plans"):
            self.validate_mutated_plan(
                lambda payload: payload.update(model="unexpected-model")
            )

    def test_task_dependencies_must_reference_known_tasks(self):
        def add_unknown_dependency(payload):
            payload["tasks"][0]["depends_on"] = ["task-that-does-not-exist"]

        with self.assertRaisesRegex(ValidationError, "unknown dependencies"):
            self.validate_mutated_plan(add_unknown_dependency)

    def test_critical_fields_must_match_critical_tasks(self):
        with self.assertRaisesRegex(ValidationError, "critical_fields"):
            self.validate_mutated_plan(
                lambda payload: payload.update(critical_fields=[])
            )

    def test_older_schema_plans_remain_readable(self):
        for version in ("1.0.0", "1.1.0"):
            with self.subTest(version=version):
                legacy_payload = self.plan.model_dump(mode="json")
                legacy_payload["schema_version"] = version

                legacy_plan = ResearchPlan.model_validate(legacy_payload)

                self.assertEqual(legacy_plan.schema_version, version)

    def test_multiple_provider_calls_in_one_agent_iteration_use_call_index(self):
        payload = self.plan.model_dump(mode="json")
        payload.update(generated_by="openai", model="test-model")
        base_usage = AgentIterationUsage(
            agent="planner",
            iteration=1,
            requested_model="test-model",
            resolved_model="test-model",
            tokens=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        )
        payload["agent_usage"] = [
            base_usage.model_dump(mode="json"),
            base_usage.model_copy(update={"call_index": 2}).model_dump(mode="json"),
        ]

        plan = ResearchPlan.model_validate(payload)

        self.assertEqual([item.call_index for item in plan.agent_usage], [1, 2])

    def test_duplicate_agent_iteration_call_index_is_rejected(self):
        payload = self.plan.model_dump(mode="json")
        payload.update(generated_by="openai", model="test-model")
        usage = AgentIterationUsage(
            agent="planner",
            iteration=1,
            requested_model="test-model",
            resolved_model="test-model",
            tokens=TokenUsage(
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
            ),
        ).model_dump(mode="json")
        payload["agent_usage"] = [usage, usage]

        with self.assertRaisesRegex(ValidationError, "unique"):
            ResearchPlan.model_validate(payload)
