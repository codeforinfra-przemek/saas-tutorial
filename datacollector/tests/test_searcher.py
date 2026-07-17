from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from pydantic import ValidationError

from datacollector.agents.planner import PlannerAgent
from datacollector.agents.searcher import SearcherAgent, SearcherValidationError
from datacollector.catalog import load_question_catalog
from datacollector.llm.protocol import (
    ProviderSearchSource,
    SearcherGeneration,
)
from datacollector.llm.pricing import build_web_search_tool_usage
from datacollector.schemas import (
    AgentIterationUsage,
    PlannerInput,
    SearchAction,
    SearchTaskStatus,
    SearchResults,
    SearcherDraft,
    SearcherSourceDraft,
    SearcherTaskDraft,
    SourceType,
    TokenUsage,
)
from datacollector.storage.json_store import (
    load_research_plan,
    save_research_plan,
    save_search_results,
)


class FakeSearcherLLM:
    model_name = "fake-searcher-model"

    def __init__(self):
        self.calls = []

    def generate(
        self,
        plan,
        tasks,
        system_prompt,
        *,
        iteration,
        max_search_calls,
    ):
        self.calls.append(
            (plan, tasks, system_prompt, iteration, max_search_calls)
        )
        task = tasks[0]
        query = task.search_queries[0]
        provider_url = "https://example.com/franchise?utm_source=search"
        draft = SearcherDraft(
            warnings=[],
            sources=[
                SearcherSourceDraft(
                    url="https://example.com/franchise",
                    title="Official franchise page",
                    source_type=SourceType.OFFICIAL,
                    task_ids=[task.task_id],
                    relevance_note="Candidate official material for this task.",
                ),
                SearcherSourceDraft(
                    url="https://hallucinated.invalid/not-consulted",
                    title="Must be rejected",
                    source_type=SourceType.UNKNOWN,
                    task_ids=[task.task_id],
                    relevance_note="Not provider grounded.",
                ),
            ],
            task_results=[
                SearcherTaskDraft(
                    task_id=task.task_id,
                    status=SearchTaskStatus.SOURCES_FOUND,
                    attempted_queries=[query, "query never issued"],
                    source_urls=[
                        "https://example.com/franchise",
                        "https://hallucinated.invalid/not-consulted",
                    ],
                    notes="One provider-grounded candidate was located.",
                )
            ],
        )
        return SearcherGeneration(
            draft=draft,
            usage=AgentIterationUsage(
                agent="searcher",
                iteration=iteration,
                requested_model=self.model_name,
                resolved_model=self.model_name,
                tokens=TokenUsage(
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                ),
                tool_usage=[build_web_search_tool_usage({"search": 1})],
            ),
            actions=[
                SearchAction(
                    action_id="ws_test",
                    action_type="search",
                    queries=[query],
                    source_urls=[provider_url],
                )
            ],
            provider_sources=[
                ProviderSearchSource(
                    url=provider_url,
                    title="Provider title",
                ),
                ProviderSearchSource(
                    url="http://127.0.0.1/private",
                    title="Private address must be rejected",
                ),
            ],
        )


class FakeUnconfirmedNotFoundLLM(FakeSearcherLLM):
    def generate(
        self,
        plan,
        tasks,
        system_prompt,
        *,
        iteration,
        max_search_calls,
    ):
        generation = super().generate(
            plan,
            tasks,
            system_prompt,
            iteration=iteration,
            max_search_calls=max_search_calls,
        )
        task = tasks[0]
        draft = SearcherDraft(
            warnings=[],
            sources=[],
            task_results=[
                SearcherTaskDraft(
                    task_id=task.task_id,
                    status=SearchTaskStatus.NO_SOURCES_FOUND,
                    attempted_queries=["query not present in provider actions"],
                    source_urls=[],
                    notes="The model claimed no result without query provenance.",
                )
            ],
        )
        return SearcherGeneration(
            draft=draft,
            usage=generation.usage,
            actions=generation.actions,
            provider_sources=[],
        )


class SearcherAgentTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = PlannerAgent(load_question_catalog()).create_plan(
            PlannerInput(brand_name="Żabka", depth="catalog")
        )

    def test_free_searcher_is_explicit_query_workload_without_network_claim(self):
        results = SearcherAgent().create_search_results(
            self.plan,
            plan_sha256="a" * 64,
            plan_reference="/tmp/plan-free.json",
            task_limit=2,
            max_search_calls=3,
        )

        self.assertEqual(results.generated_by, "offline")
        self.assertFalse(results.search_executed)
        self.assertEqual(results.agent_usage, [])
        self.assertEqual(len(results.task_results), 2)
        self.assertEqual(results.sources, [])
        self.assertTrue(
            all(
                item.status == SearchTaskStatus.QUERY_WORKLOAD_ONLY
                for item in results.task_results
            )
        )
        self.assertTrue(
            all(not source.provider_verified for source in results.sources)
        )
        self.assertTrue(any("no network search" in item for item in results.warnings))

    def test_free_searcher_seeds_only_known_brand_url_not_framework_references(self):
        plan = PlannerAgent(load_question_catalog()).create_plan(
            PlannerInput(
                brand_name="Example",
                depth="catalog",
                known_official_website="https://example.com/franchise",
            )
        )

        results = SearcherAgent().create_search_results(
            plan,
            plan_sha256="1" * 64,
            plan_reference="/tmp/plan-free.json",
            task_limit=1,
        )

        self.assertEqual(len(results.sources), 1)
        self.assertEqual(
            results.sources[0].canonical_url,
            "https://example.com/franchise",
        )
        self.assertEqual(results.sources[0].source_type, SourceType.OFFICIAL)

    def test_paid_searcher_keeps_only_provider_grounded_urls(self):
        llm = FakeSearcherLLM()
        results = SearcherAgent(llm).create_search_results(
            self.plan,
            plan_sha256="b" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
            max_search_calls=4,
        )

        self.assertTrue(results.search_executed)
        self.assertEqual(results.generated_by, "openai")
        self.assertEqual(len(results.sources), 1)
        self.assertEqual(
            results.sources[0].canonical_url,
            "https://example.com/franchise",
        )
        self.assertTrue(results.sources[0].provider_verified)
        self.assertEqual(
            results.task_results[0].status,
            SearchTaskStatus.SOURCES_FOUND,
        )
        self.assertEqual(
            results.task_results[0].attempted_queries,
            [results.task_results[0].planned_queries[0]],
        )
        self.assertTrue(
            any("not confirmed" in warning for warning in results.warnings)
        )
        self.assertTrue(
            any("non-public" in warning for warning in results.warnings)
        )
        self.assertEqual(llm.calls[0][3:], (1, 4))

    def test_search_artifact_schema_rejects_broken_audit_invariants(self):
        results = SearcherAgent(FakeSearcherLLM()).create_search_results(
            self.plan,
            plan_sha256="2" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
        )

        mutations = (
            lambda payload: payload["task_results"][0].update(source_ids=[]),
            lambda payload: payload["sources"][0].update(provider_verified=False),
            lambda payload: payload["agent_usage"][0].update(tool_usage=[]),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                payload = results.model_dump(mode="json")
                mutate(payload)
                with self.assertRaises(ValidationError):
                    SearchResults.model_validate(payload)

    def test_unknown_task_selector_is_rejected_before_paid_call(self):
        llm = FakeSearcherLLM()

        with self.assertRaisesRegex(SearcherValidationError, "Unknown"):
            SearcherAgent(llm).create_search_results(
                self.plan,
                plan_sha256="c" * 64,
                plan_reference="/tmp/plan.json",
                requested_task_ids=["invented-task"],
            )

        self.assertEqual(llm.calls, [])

    def test_explicit_task_selection_is_not_silently_truncated(self):
        task_ids = [task.task_id for task in self.plan.tasks[:6]]

        with self.assertRaisesRegex(SearcherValidationError, "increase the limit"):
            SearcherAgent().create_search_results(
                self.plan,
                plan_sha256="e" * 64,
                plan_reference="/tmp/plan.json",
                requested_task_ids=task_ids,
                task_limit=5,
            )

    def test_no_sources_found_requires_confirmed_attempted_query(self):
        results = SearcherAgent(FakeUnconfirmedNotFoundLLM()).create_search_results(
            self.plan,
            plan_sha256="f" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
        )

        self.assertEqual(
            results.task_results[0].status,
            SearchTaskStatus.NOT_SEARCHED,
        )
        self.assertEqual(results.task_results[0].attempted_queries, [])

    def test_old_plan_placeholder_query_is_not_sent_or_saved(self):
        first_task = self.plan.tasks[0].model_copy(
            update={
                "search_queries": [
                    *self.plan.tasks[0].search_queries,
                    '"[verified legal name]" bankruptcy',
                ]
            }
        )
        plan = self.plan.model_copy(
            update={"tasks": [first_task, *self.plan.tasks[1:]]}
        )

        results = SearcherAgent().create_search_results(
            plan,
            plan_sha256="d" * 64,
            plan_reference="/tmp/legacy-plan.json",
            task_limit=1,
        )

        self.assertNotIn(
            '"[verified legal name]" bankruptcy',
            results.task_results[0].planned_queries,
        )
        self.assertTrue(
            any("unresolved placeholders" in warning for warning in results.warnings)
        )

    def test_artifact_names_preserve_free_marker_and_never_overwrite(self):
        with TemporaryDirectory() as temporary_directory:
            plan_path = save_research_plan(self.plan, temporary_directory)
            loaded_plan, plan_hash = load_research_plan(plan_path)
            results = SearcherAgent().create_search_results(
                loaded_plan,
                plan_sha256=plan_hash,
                plan_reference=str(plan_path),
                task_limit=1,
            )
            result_path = save_search_results(results, plan_path)

            self.assertEqual(plan_path.name, "plan-free.json")
            self.assertEqual(result_path.name, "sources-free.json")
            self.assertEqual(results.plan_sha256, plan_hash)
            with self.assertRaises(FileExistsError):
                save_search_results(results, plan_path)

            second_round = results.model_copy(
                update={"search_id": results.search_id, "iteration": 2}
            )
            second_path = save_search_results(second_round, plan_path)
            self.assertEqual(second_path.name, "sources-r002-free.json")
            self.assertTrue(Path(second_path).exists())
