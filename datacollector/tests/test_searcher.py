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
    SearcherProviderError,
)
from datacollector.llm.pricing import build_web_search_tool_usage
from datacollector.schemas import (
    AgentIterationUsage,
    PlannerInput,
    SearchAction,
    SearchQueryCoverage,
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
        call_index,
        max_search_calls,
        min_queries_per_task,
    ):
        self.calls.append(
            (
                plan,
                tasks,
                system_prompt,
                iteration,
                call_index,
                max_search_calls,
                min_queries_per_task,
            )
        )
        task = tasks[0]
        query = task.search_queries[0]
        provider_url = (
            "https://example.com/franchise?trk=campaign&utm_source=search"
        )
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
                    unresolved_targets=[],
                    notes="One provider-grounded candidate was located.",
                )
            ],
        )
        return SearcherGeneration(
            draft=draft,
            usage=AgentIterationUsage(
                agent="searcher",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=[task.task_id],
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
                    call_index=call_index,
                    scope_task_ids=[task.task_id],
                    action_type="search",
                    queries=[query],
                    source_urls=[
                        provider_url,
                        "https://example.com/unmapped-result",
                    ],
                )
            ],
            provider_sources=[
                ProviderSearchSource(
                    url=provider_url,
                    title="Provider title",
                ),
                ProviderSearchSource(
                    url="https://example.com/unmapped-result",
                    title="Raw but unassigned result",
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
        call_index,
        max_search_calls,
        min_queries_per_task,
    ):
        generation = super().generate(
            plan,
            tasks,
            system_prompt,
            iteration=iteration,
            call_index=call_index,
            max_search_calls=max_search_calls,
            min_queries_per_task=min_queries_per_task,
        )
        task = tasks[1]
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
            usage=generation.usage.model_copy(
                update={"scope_task_ids": [item.task_id for item in tasks]}
            ),
            actions=[
                generation.actions[0].model_copy(
                    update={
                        "queries": [tasks[0].search_queries[0]],
                        "scope_task_ids": [item.task_id for item in tasks],
                        "source_urls": [],
                    }
                )
            ],
            provider_sources=[],
        )


class FakeCaseChangedQueryLLM(FakeSearcherLLM):
    def generate(self, *args, **kwargs):
        generation = super().generate(*args, **kwargs)
        changed_query = generation.actions[0].queries[0].upper()
        return SearcherGeneration(
            draft=generation.draft,
            usage=generation.usage,
            actions=[
                generation.actions[0].model_copy(
                    update={"queries": [changed_query]}
                )
            ],
            provider_sources=generation.provider_sources,
        )


class FakeInvalidActionScopeLLM(FakeSearcherLLM):
    def generate(self, *args, **kwargs):
        generation = super().generate(*args, **kwargs)
        return SearcherGeneration(
            draft=generation.draft,
            usage=generation.usage,
            actions=[
                generation.actions[0].model_copy(
                    update={"scope_task_ids": ["unknown-task"]}
                )
            ],
            provider_sources=generation.provider_sources,
        )


class FakeRetryingSearcherLLM:
    model_name = "fake-retrying-searcher"

    def __init__(
        self,
        *,
        fail_retry=False,
        fail_retry_without_usage=False,
        same_domain_subdomains=False,
    ):
        self.calls = []
        self.fail_retry = fail_retry
        self.fail_retry_without_usage = fail_retry_without_usage
        self.same_domain_subdomains = same_domain_subdomains

    def generate(
        self,
        plan,
        tasks,
        system_prompt,
        *,
        iteration,
        call_index,
        max_search_calls,
        min_queries_per_task,
    ):
        self.calls.append(
            (call_index, [task.task_id for task in tasks], max_search_calls)
        )
        task = tasks[0]
        if call_index == 2 and self.fail_retry_without_usage:
            raise SearcherProviderError(
                "Retry response omitted token usage.",
                code="invalid_usage",
                observed_tool_calls=1,
                tool_usage=[build_web_search_tool_usage({"search": 1})],
            )
        if call_index == 2 and self.fail_retry:
            usage = AgentIterationUsage(
                agent="searcher",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=[task.task_id],
                requested_model=self.model_name,
                resolved_model=self.model_name,
                tokens=TokenUsage(
                    input_tokens=50,
                    output_tokens=10,
                    total_tokens=60,
                ),
                tool_usage=[build_web_search_tool_usage({"search": 1})],
            )
            raise SearcherProviderError(
                "Retry response was unusable.",
                code="retry_unusable",
                usage=usage,
                observed_tool_calls=1,
            )

        query_index = min(call_index - 1, len(task.search_queries) - 1)
        query = task.search_queries[query_index]
        if self.same_domain_subdomains:
            host = "example.com" if call_index == 1 else "registry.example.com"
            url = f"https://{host}/source-{call_index}"
        else:
            host = (
                "official.example.com"
                if call_index == 1
                else "registry.example.org"
            )
            url = f"https://{host}/source-{call_index}"
        source_type = (
            SourceType.OFFICIAL if call_index == 1 else SourceType.REGISTRY
        )
        unresolved_targets = (
            ["official registry extract"] if call_index == 1 else []
        )
        status = (
            SearchTaskStatus.PARTIAL
            if call_index == 1
            else SearchTaskStatus.SOURCES_FOUND
        )
        return SearcherGeneration(
            draft=SearcherDraft(
                warnings=[],
                sources=[
                    SearcherSourceDraft(
                        url=url,
                        title=f"Source {call_index}",
                        source_type=source_type,
                        task_ids=[task.task_id],
                        relevance_note="Task-specific candidate.",
                    )
                ],
                task_results=[
                    SearcherTaskDraft(
                        task_id=task.task_id,
                        status=status,
                        attempted_queries=[query],
                        source_urls=[url],
                        unresolved_targets=unresolved_targets,
                        notes=f"Call {call_index} result.",
                    )
                ],
            ),
            usage=AgentIterationUsage(
                agent="searcher",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=[task.task_id],
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
                    action_id=f"ws_retry_{call_index}",
                    call_index=call_index,
                    scope_task_ids=[task.task_id],
                    action_type="search",
                    queries=[query],
                    source_urls=[url],
                )
            ],
            provider_sources=[
                ProviderSearchSource(url=url, title=f"Source {call_index}")
            ],
        )


class FakeBatchSearcherLLM:
    model_name = "fake-batch-searcher"

    def generate(
        self,
        plan,
        tasks,
        system_prompt,
        *,
        iteration,
        call_index,
        max_search_calls,
        min_queries_per_task,
    ):
        first_task, second_task = tasks
        first_query = first_task.search_queries[0]
        derived_second_query = "custom query for the second task"
        first_url = "https://example.com/identity"
        second_url = "https://example.com/offer"
        return SearcherGeneration(
            draft=SearcherDraft(
                warnings=[],
                sources=[
                    SearcherSourceDraft(
                        url=first_url,
                        source_type=SourceType.OFFICIAL,
                        task_ids=[first_task.task_id],
                    ),
                    SearcherSourceDraft(
                        url=second_url,
                        source_type=SourceType.OFFICIAL,
                        task_ids=[second_task.task_id],
                    ),
                ],
                task_results=[
                    SearcherTaskDraft(
                        task_id=first_task.task_id,
                        status=SearchTaskStatus.SOURCES_FOUND,
                        attempted_queries=[first_query],
                        source_urls=[first_url],
                    ),
                    SearcherTaskDraft(
                        task_id=second_task.task_id,
                        status=SearchTaskStatus.SOURCES_FOUND,
                        attempted_queries=[derived_second_query],
                        source_urls=[second_url],
                    ),
                ],
            ),
            usage=AgentIterationUsage(
                agent="searcher",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=[task.task_id for task in tasks],
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
                    action_id="ws_batch",
                    call_index=call_index,
                    scope_task_ids=[task.task_id for task in tasks],
                    action_type="search",
                    queries=[first_query, derived_second_query],
                    source_urls=[first_url, second_url],
                )
            ],
            provider_sources=[
                ProviderSearchSource(url=first_url),
                ProviderSearchSource(url=second_url),
            ],
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
            all(not source.provider_observed for source in results.sources)
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
        self.assertTrue(results.sources[0].provider_observed)
        self.assertNotIn("trk=", results.sources[0].canonical_url)
        self.assertIn(
            "https://example.com/unmapped-result",
            results.actions[0].source_urls,
        )
        self.assertNotIn(
            "https://example.com/unmapped-result",
            [source.canonical_url for source in results.sources],
        )
        self.assertEqual(
            results.task_results[0].status,
            SearchTaskStatus.PARTIAL,
        )
        self.assertIn(
            "source_candidates:1/2",
            results.task_results[0].coverage_gaps,
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
        self.assertEqual(llm.calls[0][3:], (1, 1, 4, 1))

    def test_quality_retry_is_opt_in_and_closes_partial_coverage(self):
        no_retry_llm = FakeRetryingSearcherLLM()
        no_retry = SearcherAgent(no_retry_llm).create_search_results(
            self.plan,
            plan_sha256="7" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
            max_search_calls=3,
            min_queries_per_task=2,
        )

        self.assertEqual(len(no_retry_llm.calls), 1)
        self.assertEqual(no_retry.task_results[0].status, SearchTaskStatus.PARTIAL)
        self.assertEqual(
            no_retry.task_results[0].query_coverage,
            SearchQueryCoverage.PARTIAL,
        )

        retry_llm = FakeRetryingSearcherLLM()
        retried = SearcherAgent(retry_llm).create_search_results(
            self.plan,
            plan_sha256="8" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
            max_search_calls=3,
            min_queries_per_task=2,
            max_retry_tasks=1,
            retry_search_calls=1,
        )

        self.assertEqual([call[0] for call in retry_llm.calls], [1, 2])
        self.assertEqual([call[2] for call in retry_llm.calls], [2, 1])
        self.assertEqual(
            [usage.call_index for usage in retried.agent_usage],
            [1, 2],
        )
        self.assertEqual(len(retried.actions), 2)
        self.assertEqual(len(retried.sources), 2)
        self.assertEqual(
            retried.task_results[0].query_coverage,
            SearchQueryCoverage.COMPLETE,
        )
        self.assertEqual(
            retried.task_results[0].status,
            SearchTaskStatus.SOURCES_FOUND,
        )
        self.assertEqual(retried.task_results[0].coverage_gaps, [])

    def test_failed_quality_retry_preserves_results_and_charged_usage(self):
        llm = FakeRetryingSearcherLLM(fail_retry=True)
        results = SearcherAgent(llm).create_search_results(
            self.plan,
            plan_sha256="9" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
            max_search_calls=3,
            max_retry_tasks=1,
        )

        self.assertEqual(results.task_results[0].status, SearchTaskStatus.PARTIAL)
        self.assertEqual(len(results.actions), 1)
        self.assertEqual(
            [usage.call_index for usage in results.agent_usage],
            [1, 2],
        )
        self.assertEqual(len(results.failed_attempts), 1)
        self.assertEqual(results.failed_attempts[0].call_index, 2)
        self.assertTrue(results.failed_attempts[0].usage_recorded)

    def test_required_corroboration_needs_candidate_domain_diversity(self):
        llm = FakeRetryingSearcherLLM(same_domain_subdomains=True)
        results = SearcherAgent(llm).create_search_results(
            self.plan,
            plan_sha256="5" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
            max_search_calls=3,
            min_queries_per_task=2,
            max_retry_tasks=1,
        )

        self.assertEqual(results.task_results[0].status, SearchTaskStatus.PARTIAL)
        self.assertIn(
            "independent_candidate_domains:1/2",
            results.task_results[0].coverage_gaps,
        )

    def test_failed_retry_keeps_known_tool_cost_when_tokens_are_missing(self):
        llm = FakeRetryingSearcherLLM(fail_retry_without_usage=True)
        results = SearcherAgent(llm).create_search_results(
            self.plan,
            plan_sha256="1" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
            max_search_calls=3,
            max_retry_tasks=1,
        )

        failure = results.failed_attempts[0]
        self.assertFalse(failure.usage_recorded)
        self.assertTrue(failure.token_usage_unknown)
        self.assertEqual(failure.observed_tool_calls, 1)
        self.assertEqual(failure.tool_usage[0].calls, 1)
        self.assertEqual([usage.call_index for usage in results.agent_usage], [1])

    def test_multi_task_derived_query_is_not_false_complete_provenance(self):
        results = SearcherAgent(FakeBatchSearcherLLM()).create_search_results(
            self.plan,
            plan_sha256="6" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=2,
        )

        second = results.task_results[1]
        self.assertEqual(second.attempted_queries, [])
        self.assertEqual(second.query_coverage, SearchQueryCoverage.NONE)
        self.assertEqual(second.status, SearchTaskStatus.PARTIAL)
        self.assertIn("planned_query_attempts:0/1", second.coverage_gaps)
        self.assertEqual(results.sources[1].observed_in_action_ids, ["ws_batch"])
        self.assertEqual(results.sources[1].discovered_via_queries, [])
        self.assertTrue(
            any("deterministic task attribution" in item for item in results.warnings)
        )

    def test_case_changed_query_is_derived_not_exact_plan_coverage(self):
        results = SearcherAgent(FakeCaseChangedQueryLLM()).create_search_results(
            self.plan,
            plan_sha256="4" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
        )

        task_result = results.task_results[0]
        self.assertEqual(task_result.planned_queries_attempted, [])
        self.assertEqual(len(task_result.derived_queries_attempted), 1)
        self.assertEqual(task_result.query_coverage, SearchQueryCoverage.NONE)

    def test_paid_postprocessing_error_retains_provider_usage(self):
        with self.assertRaises(SearcherProviderError) as raised:
            SearcherAgent(FakeInvalidActionScopeLLM()).create_search_results(
                self.plan,
                plan_sha256="3" * 64,
                plan_reference="/tmp/plan.json",
                task_limit=1,
            )

        self.assertEqual(raised.exception.code, "postprocessing_error")
        self.assertEqual(len(raised.exception.usages), 1)
        self.assertEqual(raised.exception.usages[0].tokens.total_tokens, 120)

    def test_schema_1_0_provider_verified_alias_still_loads(self):
        results = SearcherAgent(FakeSearcherLLM()).create_search_results(
            self.plan,
            plan_sha256="0" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
        )
        payload = results.model_dump(mode="json")
        self.assertIn("provider_observed", payload["sources"][0])
        self.assertNotIn("provider_verified", payload["sources"][0])
        payload["schema_version"] = "1.0.0"
        payload["sources"][0]["provider_verified"] = payload["sources"][0].pop(
            "provider_observed"
        )
        for source in payload["sources"]:
            source.pop("observed_in_action_ids", None)
        for action in payload["actions"]:
            action.pop("call_index", None)
            action.pop("scope_task_ids", None)
        for task_result in payload["task_results"]:
            for field in (
                "planned_queries_attempted",
                "derived_queries_attempted",
                "query_coverage",
                "minimum_query_attempts",
                "minimum_sources",
                "action_ids",
                "coverage_gaps",
                "unresolved_targets",
            ):
                task_result.pop(field, None)
        payload.pop("failed_attempts", None)

        loaded = SearchResults.model_validate(payload)

        self.assertEqual(loaded.schema_version, "1.0.0")
        self.assertTrue(loaded.sources[0].provider_observed)

    def test_search_artifact_schema_rejects_broken_audit_invariants(self):
        results = SearcherAgent(FakeSearcherLLM()).create_search_results(
            self.plan,
            plan_sha256="2" * 64,
            plan_reference="/tmp/plan.json",
            task_limit=1,
        )

        mutations = (
            lambda payload: payload["task_results"][0].update(source_ids=[]),
            lambda payload: payload["sources"][0].update(provider_observed=False),
            lambda payload: payload["sources"][0].update(
                url="https://example.com/different"
            ),
            lambda payload: payload["actions"][0].update(action_id=None),
            lambda payload: payload["actions"][0].update(status="failed"),
            lambda payload: payload["actions"][0].update(scope_task_ids=[]),
            lambda payload: payload["actions"][0].update(
                source_urls=["https://example.com/different"]
            ),
            lambda payload: payload["sources"][0].update(
                discovered_via_queries=["query never issued"]
            ),
            lambda payload: payload["task_results"][0].update(
                attempted_queries=[payload["task_results"][0]["planned_queries"][1]],
                planned_queries_attempted=[
                    payload["task_results"][0]["planned_queries"][1]
                ],
            ),
            lambda payload: payload["task_results"][0].update(
                status=SearchTaskStatus.NOT_SEARCHED.value
            ),
            lambda payload: payload["agent_usage"][0].update(scope_task_ids=[]),
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
            task_limit=2,
        )

        self.assertEqual(
            results.task_results[1].status,
            SearchTaskStatus.NOT_SEARCHED,
        )
        self.assertEqual(results.task_results[1].attempted_queries, [])

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

    def test_paid_task_without_executable_query_is_rejected_before_call(self):
        llm = FakeSearcherLLM()
        first_task = self.plan.tasks[0].model_copy(
            update={"search_queries": ['"[verified legal name]" bankruptcy']}
        )
        plan = self.plan.model_copy(
            update={"tasks": [first_task, *self.plan.tasks[1:]]}
        )

        with self.assertRaisesRegex(SearcherValidationError, "without executable"):
            SearcherAgent(llm).create_search_results(
                plan,
                plan_sha256="2" * 64,
                plan_reference="/tmp/legacy-plan.json",
                task_limit=1,
            )

        self.assertEqual(llm.calls, [])

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
