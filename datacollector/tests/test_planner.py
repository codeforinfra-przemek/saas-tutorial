import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from datacollector.agents.planner import PlannerAgent, PlannerValidationError
from datacollector.catalog import load_question_catalog, select_questions
from datacollector.llm.protocol import PlannerGeneration
from datacollector.schemas import (
    AgentIterationUsage,
    PlannerDraft,
    PlannerInput,
    PlannerTaskGuidance,
    Priority,
    QuestionCatalog,
    Requirement,
    ResearchDepth,
    TaskAction,
    TokenUsage,
)
from datacollector.storage.json_store import save_research_plan


class FakePlannerLLM:
    model_name = "fake-planner-model"

    def __init__(self, draft):
        self.draft = draft
        self.calls = []

    def generate(self, planner_input, questions, system_prompt, *, iteration):
        self.calls.append((planner_input, questions, system_prompt, iteration))
        return PlannerGeneration(
            draft=self.draft,
            usage=AgentIterationUsage(
                agent="planner",
                iteration=iteration,
                requested_model=self.model_name,
                resolved_model=self.model_name,
                response_id="resp_fake",
                request_id="req_fake",
                service_tier="default",
                tokens=TokenUsage(
                    input_tokens=100,
                    output_tokens=20,
                    reasoning_tokens=5,
                    total_tokens=120,
                ),
            ),
        )


class PlannerAgentTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_question_catalog()

    def test_offline_plan_keeps_full_fdd_coverage_for_polish_benchmark(self):
        plan = PlannerAgent(self.catalog).create_plan(
            PlannerInput(
                brand_name="Żabka",
                target_country="PL",
                depth=ResearchDepth.DUE_DILIGENCE,
            )
        )

        fdd_items = {item for task in plan.tasks for item in task.fdd_items}
        question_ids = {task.catalog_question_id for task in plan.tasks}
        self.assertEqual(fdd_items, set(range(1, 24)))
        self.assertNotIn("us.state_overlay", question_ids)
        self.assertEqual(plan.generated_by, "offline")
        self.assertIsNone(plan.model)
        self.assertEqual(plan.agent_usage, [])
        self.assertTrue(plan.stop_conditions.human_review_required)
        self.assertTrue(
            any("ecfr.gov" in source for source in plan.authoritative_sources)
        )

    def test_existing_complete_field_group_is_marked_for_verification(self):
        question = next(
            question
            for question in self.catalog.all_questions()
            if question.id == "fdd05.initial_fees"
        )
        plan = PlannerAgent(self.catalog).create_plan(
            PlannerInput(
                brand_name="Example",
                existing_fields=question.target_fields,
            )
        )
        task = next(
            task
            for task in plan.tasks
            if task.catalog_question_id == "fdd05.initial_fees"
        )

        self.assertEqual(task.action, TaskAction.VERIFY)
        self.assertEqual(task.fields_to_collect, [])
        self.assertEqual(task.fields_to_verify, question.target_fields)

    def test_partial_existing_field_group_splits_collection_and_verification(self):
        question = next(
            question
            for question in self.catalog.all_questions()
            if question.id == "fdd05.initial_fees"
        )
        existing_field = question.target_fields[0]
        plan = PlannerAgent(self.catalog).create_plan(
            PlannerInput(
                brand_name="Example",
                existing_fields=[existing_field],
            )
        )
        task = next(
            task
            for task in plan.tasks
            if task.catalog_question_id == "fdd05.initial_fees"
        )

        self.assertEqual(task.action, TaskAction.COLLECT_AND_VERIFY)
        self.assertEqual(task.fields_to_verify, [existing_field])
        self.assertEqual(
            task.fields_to_collect,
            [field for field in question.target_fields if field != existing_field],
        )

    def test_llm_guidance_is_merged_but_cannot_downgrade_critical_task(self):
        draft = PlannerDraft(
            objective="Build a market-specific and auditable research plan.",
            planning_notes=["Start with the current agreement version."],
            assumptions=[],
            scope_warnings=[],
            task_guidance=[
                PlannerTaskGuidance(
                    catalog_question_id="fdd05.initial_fees",
                    priority=Priority.LOW,
                    rationale="The fee schedule may vary by unit format.",
                    search_queries=["Example custom initial fee query"],
                    source_hints=["Current fee schedule"],
                )
            ],
        )
        llm = FakePlannerLLM(draft)
        plan = PlannerAgent(self.catalog, llm).create_plan(
            PlannerInput(brand_name="Example", max_queries_per_task=5)
        )
        task = next(
            task
            for task in plan.tasks
            if task.catalog_question_id == "fdd05.initial_fees"
        )

        self.assertEqual(plan.generated_by, "openai")
        self.assertEqual(plan.model, "fake-planner-model")
        self.assertEqual(task.priority, Priority.CRITICAL)
        self.assertIn("Example custom initial fee query", task.search_queries)
        self.assertEqual(task.source_hints, ["Current fee schedule"])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(plan.agent_usage[0].agent, "planner")
        self.assertEqual(plan.agent_usage[0].iteration, 1)

    def test_llm_queries_with_unresolved_placeholders_are_removed(self):
        llm = FakePlannerLLM(
            PlannerDraft(
                objective="Build a complete and auditable research plan.",
                planning_notes=[],
                assumptions=[],
                scope_warnings=[],
                task_guidance=[
                    PlannerTaskGuidance(
                        catalog_question_id="fdd04.bankruptcy",
                        priority=Priority.HIGH,
                        rationale="Identity must be established before registry search.",
                        search_queries=[
                            '"[verified legal name]" bankruptcy',
                            '"Example" bankruptcy registry',
                        ],
                        source_hints=[],
                    )
                ],
            )
        )

        plan = PlannerAgent(self.catalog, llm).create_plan(
            PlannerInput(brand_name="Example", max_queries_per_task=5)
        )
        task = next(
            task
            for task in plan.tasks
            if task.catalog_question_id == "fdd04.bankruptcy"
        )

        self.assertNotIn('"[verified legal name]" bankruptcy', task.search_queries)
        self.assertIn('"Example" bankruptcy registry', task.search_queries)
        self.assertTrue(
            any("unresolved placeholders" in note for note in plan.planning_notes)
        )

    def test_repeated_adjacent_query_terms_are_normalized(self):
        llm = FakePlannerLLM(
            PlannerDraft(
                objective="Build a complete and auditable research plan.",
                planning_notes=[],
                assumptions=[],
                scope_warnings=[],
                task_guidance=[
                    PlannerTaskGuidance(
                        catalog_question_id="scope.brand_identity",
                        priority=Priority.CRITICAL,
                        rationale="Resolve the offering entity.",
                        search_queries=['"Example" "Example" registry PL PL'],
                        source_hints=[],
                    )
                ],
            )
        )

        plan = PlannerAgent(self.catalog, llm).create_plan(
            PlannerInput(brand_name="Example", max_queries_per_task=5)
        )
        task = next(
            task
            for task in plan.tasks
            if task.catalog_question_id == "scope.brand_identity"
        )

        self.assertIn('"Example" registry PL', task.search_queries)
        self.assertNotIn('"Example" "Example" registry PL PL', task.search_queries)
        self.assertTrue(
            any("Normalized" in note for note in plan.planning_notes)
        )

    def test_llm_priority_escalation_adds_task_fields_to_critical_gate(self):
        planner_input = PlannerInput(brand_name="Example")
        question = next(
            question
            for _, question in select_questions(self.catalog, planner_input)
            if question.requirement == Requirement.REQUIRED
        )
        llm = FakePlannerLLM(
            PlannerDraft(
                objective="Build a complete and auditable research plan.",
                planning_notes=[],
                assumptions=[],
                scope_warnings=[],
                task_guidance=[
                    PlannerTaskGuidance(
                        catalog_question_id=question.id,
                        priority=Priority.CRITICAL,
                        rationale="This scope makes the evidence a blocking dependency.",
                        search_queries=[],
                        source_hints=[],
                    )
                ],
            )
        )

        plan = PlannerAgent(self.catalog, llm).create_plan(planner_input)

        self.assertTrue(set(question.target_fields).issubset(plan.critical_fields))

    def test_missing_selected_dependency_is_rejected_before_llm_call(self):
        payload = self.catalog.model_dump(mode="json")
        questions = [
            question
            for section in payload["sections"]
            for question in section["questions"]
        ]
        selected_question = next(
            question
            for question in questions
            if question["minimum_depth"] == ResearchDepth.CATALOG.value
            and not question["dependencies"]
            and question["jurisdiction"] == "all"
            and question["sensitivity"] != "personal_data"
        )
        deeper_question = {
            **selected_question,
            "id": "test.future_dependency",
            "title": "Future depth dependency",
            "question": "Collect a field that is intentionally outside catalog depth.",
            "fdd_items": [],
            "minimum_depth": ResearchDepth.UNIT.value,
            "requirement": "optional",
            "target_fields": ["test.future_dependency"],
            "dependencies": [],
        }
        payload["sections"][0]["questions"].append(deeper_question)
        selected_question["dependencies"] = [deeper_question["id"]]
        catalog = QuestionCatalog.model_validate(payload)
        llm = FakePlannerLLM(
            PlannerDraft(
                objective="This draft must never be requested from the provider.",
                planning_notes=[],
                assumptions=[],
                scope_warnings=[],
                task_guidance=[],
            )
        )

        with self.assertRaisesRegex(PlannerValidationError, "dependencies"):
            PlannerAgent(catalog, llm).create_plan(
                PlannerInput(brand_name="Example", depth=ResearchDepth.CATALOG)
            )

        self.assertEqual(llm.calls, [])

    def test_unknown_llm_question_id_is_rejected(self):
        llm = FakePlannerLLM(
            PlannerDraft(
                objective="Build a complete and auditable research plan.",
                planning_notes=[],
                assumptions=[],
                scope_warnings=[],
                task_guidance=[
                    PlannerTaskGuidance(
                        catalog_question_id="invented.question",
                        priority=Priority.HIGH,
                        rationale="This identifier is not canonical.",
                        search_queries=[],
                        source_hints=[],
                    )
                ],
            )
        )

        with self.assertRaisesRegex(PlannerValidationError, "unknown questions"):
            PlannerAgent(self.catalog, llm).create_plan(
                PlannerInput(brand_name="Example")
            )

    def test_plan_artifact_is_saved_as_valid_json(self):
        plan = PlannerAgent(self.catalog).create_plan(
            PlannerInput(brand_name="Żabka", depth=ResearchDepth.CATALOG)
        )
        with TemporaryDirectory() as temporary_directory:
            plan_path = save_research_plan(plan, temporary_directory)
            payload = json.loads(plan_path.read_text(encoding="utf-8"))

            self.assertEqual(plan_path.name, "plan-free.json")
            self.assertIn("zabka", str(plan_path.parent.parent))
            self.assertEqual(payload["run_id"], plan.run_id)
            self.assertEqual(payload["schema_version"], "1.2.0")
            self.assertTrue(payload["tasks"])
