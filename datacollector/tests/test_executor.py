from unittest import TestCase

from datacollector.agents.executor import ExecutorAgent
from datacollector.agents.extractor import ExtractorAgent
from datacollector.agents.searcher import SearcherAgent
from datacollector.schemas import (
    ExecutorBatchStatus,
    ExecutorMode,
    ExecutorNextAction,
    ResolverAction,
    ResolverResults,
    SearchSourceOrigin,
)
from datacollector.tests import test_checker as checker_fixtures
from datacollector.tests import test_resolver as resolver_fixtures
from datacollector.tests import test_searcher as searcher_fixtures


class UnexpectedFetcher:
    def fetch(self, url, *, source_id=""):
        raise AssertionError(f"Cached source was fetched unexpectedly: {source_id}")


class ExecutorAgentTests(TestCase):
    @classmethod
    def setUpClass(cls):
        resolver_fixtures.ResolverAgentTests.setUpClass()
        cls.plan = resolver_fixtures.ResolverAgentTests.plan
        cls.search = resolver_fixtures.ResolverAgentTests.search_results
        cls.extraction = resolver_fixtures.ResolverAgentTests.extraction_results
        cls.checker = resolver_fixtures.ResolverAgentTests.checker_results
        cls.resolution = resolver_fixtures.ResolverAgentTests()._run()

    def test_free_execution_reuses_document_and_materializes_merged_state(self):
        merged_search, merged_extraction, execution = ExecutorAgent(
            SearcherAgent(),
            ExtractorAgent(UnexpectedFetcher()),
        ).execute(
            self.plan,
            self.search,
            self.extraction,
            self.checker,
            self.resolution,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            prior_search_sha256=checker_fixtures.SEARCH_SHA256,
            prior_extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            check_sha256="d" * 64,
            resolution_sha256="e" * 64,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            prior_search_reference=checker_fixtures.SEARCH_REFERENCE,
            prior_extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            check_reference="/fixtures/check-r004.json",
            resolution_reference="/fixtures/resolution-r004-free.json",
            merged_search_reference="/fixtures/sources-r005-free.json",
            merged_extraction_reference="/fixtures/extractions-r005-free.json",
            iteration=5,
            execution_mode=ExecutorMode.FREE,
        )

        self.assertEqual(merged_search.generated_by, "executor")
        self.assertEqual(merged_search.execution_mode, "free")
        self.assertFalse(merged_search.search_executed)
        self.assertTrue(
            all(
                source.origin == SearchSourceOrigin.INHERITED
                for source in merged_search.sources
            )
        )
        self.assertEqual(merged_extraction.generated_by, "executor")
        self.assertEqual(merged_extraction.execution_mode, "free")
        self.assertEqual(
            set(merged_extraction.selected_source_ids),
            set(self.extraction.selected_source_ids),
        )
        self.assertEqual(
            set(merged_extraction.processed_source_ids),
            set(execution.processed_source_ids),
        )
        self.assertFalse(execution.provider_executed)
        self.assertTrue(execution.ready_for_checker)
        self.assertEqual(
            execution.recommended_next_action,
            ExecutorNextAction.RUN_CHECKER,
        )
        self.assertEqual(
            execution.batch_results[0].status,
            ExecutorBatchStatus.PARTIAL,
        )

    def test_iteration_must_advance_beyond_resolver(self):
        with self.assertRaisesRegex(ValueError, "greater than the Resolver"):
            ExecutorAgent(
                SearcherAgent(),
                ExtractorAgent(UnexpectedFetcher()),
            ).execute(
                self.plan,
                self.search,
                self.extraction,
                self.checker,
                self.resolution,
                plan_sha256=checker_fixtures.PLAN_SHA256,
                prior_search_sha256=checker_fixtures.SEARCH_SHA256,
                prior_extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
                check_sha256="d" * 64,
                resolution_sha256="e" * 64,
                plan_reference=checker_fixtures.PLAN_REFERENCE,
                prior_search_reference=checker_fixtures.SEARCH_REFERENCE,
                prior_extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
                check_reference="/fixtures/check-r004.json",
                resolution_reference="/fixtures/resolution-r004-free.json",
                merged_search_reference="/fixtures/sources-r004-free.json",
                merged_extraction_reference="/fixtures/extractions-r004-free.json",
                iteration=4,
                execution_mode=ExecutorMode.FREE,
            )

    def test_paid_execution_reextracts_cached_source_and_records_only_new_usage(self):
        merged_search, merged_extraction, execution = ExecutorAgent(
            SearcherAgent(),
            ExtractorAgent(
                UnexpectedFetcher(),
                checker_fixtures.FixtureExtractorLLM(),
            ),
        ).execute(
            self.plan,
            self.search,
            self.extraction,
            self.checker,
            self.resolution,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            prior_search_sha256=checker_fixtures.SEARCH_SHA256,
            prior_extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            check_sha256="d" * 64,
            resolution_sha256="e" * 64,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            prior_search_reference=checker_fixtures.SEARCH_REFERENCE,
            prior_extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            check_reference="/fixtures/check-r004.json",
            resolution_reference="/fixtures/resolution-r004.json",
            merged_search_reference="/fixtures/sources-r005.json",
            merged_extraction_reference="/fixtures/extractions-r005.json",
            iteration=5,
            execution_mode=ExecutorMode.PAID,
        )

        self.assertFalse(merged_search.search_executed)
        self.assertTrue(merged_extraction.provider_executed)
        self.assertTrue(merged_extraction.agent_usage)
        self.assertEqual(
            execution.agent_usage,
            merged_extraction.agent_usage,
        )
        self.assertTrue(
            all(usage.iteration == 5 for usage in execution.agent_usage)
        )
        self.assertTrue(execution.provider_executed)
        self.assertEqual(execution.preserved_processed_source_ids, [])
        self.assertEqual(
            execution.batch_results[0].status,
            ExecutorBatchStatus.COMPLETED,
        )
        self.assertTrue(
            any(
                "exact predecessor Extractor artifact recorded by Executor"
                in warning
                for warning in execution.warnings
            )
        )
        self.assertFalse(
            any("prior free Extractor artifact" in warning for warning in execution.warnings)
        )

    def test_paid_search_batch_uses_resolver_query_and_adds_new_source(self):
        original_item = self.resolution.work_items[0]
        resolver_query = '"Żabka" aktualny dokument franczyzowy 2026'
        changed_item = original_item.model_copy(
            update={
                "allowed_actions": [ResolverAction.SEARCH_NEW_SOURCE],
                "selected_action": ResolverAction.SEARCH_NEW_SOURCE,
                "selected_source_ids": [],
                "queries": [resolver_query],
            }
        )
        original_batch = self.resolution.execution_batches[0]
        changed_batch = original_batch.model_copy(
            update={
                "action": ResolverAction.SEARCH_NEW_SOURCE,
                "source_ids": [],
                "queries": [resolver_query],
            }
        )
        payload = self.resolution.model_dump(mode="python")
        payload.update(
            {
                "work_items": [changed_item],
                "execution_batches": [changed_batch],
                "execution_source_ids": [],
                "search_task_ids": [changed_item.task_id],
            }
        )
        search_resolution = ResolverResults.model_validate(payload)
        search_llm = searcher_fixtures.FakeSearcherLLM()

        merged_search, merged_extraction, execution = ExecutorAgent(
            SearcherAgent(search_llm),
            ExtractorAgent(
                checker_fixtures.FixtureFetcher(),
                checker_fixtures.FixtureExtractorLLM(),
            ),
        ).execute(
            self.plan,
            self.search,
            self.extraction,
            self.checker,
            search_resolution,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            prior_search_sha256=checker_fixtures.SEARCH_SHA256,
            prior_extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            check_sha256="d" * 64,
            resolution_sha256="e" * 64,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            prior_search_reference=checker_fixtures.SEARCH_REFERENCE,
            prior_extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            check_reference="/fixtures/check-r004.json",
            resolution_reference="/fixtures/resolution-r004.json",
            merged_search_reference="/fixtures/sources-r005.json",
            merged_extraction_reference="/fixtures/extractions-r005.json",
            iteration=5,
            execution_mode=ExecutorMode.PAID,
        )

        self.assertEqual(search_llm.calls[0][1][0].search_queries, [resolver_query])
        self.assertTrue(merged_search.search_executed)
        self.assertEqual(len(execution.new_source_ids), 1)
        self.assertIn(
            execution.new_source_ids[0],
            merged_extraction.selected_source_ids,
        )
        self.assertEqual(
            {usage.agent for usage in execution.agent_usage},
            {"searcher", "extractor"},
        )
