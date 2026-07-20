from unittest import TestCase

from datacollector.agents.executor import (
    ExecutorAgent,
    _search_sources_requiring_extraction,
)
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
        base_resolution = resolver_fixtures.ResolverAgentTests()._run()
        source_id = cls.search.sources[0].source_id
        reextract_item = base_resolution.work_items[0].model_copy(
            update={
                "allowed_actions": [
                    ResolverAction.REEXTRACT_EXISTING,
                    ResolverAction.SEARCH_NEW_SOURCE,
                    ResolverAction.HUMAN_REVIEW,
                ],
                "selected_action": ResolverAction.REEXTRACT_EXISTING,
                "selected_source_ids": [source_id],
                "fallback_source_ids": [],
            }
        )
        resolution_payload = base_resolution.model_dump(mode="python")
        resolution_payload.update(
            {
                "work_items": [reextract_item],
                "execution_batches": (
                    resolver_fixtures.ResolverAgent._build_batches(
                        [reextract_item]
                    )
                ),
                "execution_source_ids": [source_id],
                "search_task_ids": [],
            }
        )
        cls.resolution = ResolverResults.model_validate(resolution_payload)

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

    def test_rediscovered_source_is_not_reextracted_without_new_task_scope(self):
        process_ids, skipped_ids = _search_sources_requiring_extraction(
            self.search,
            self.extraction,
        )

        self.assertEqual(process_ids, [])
        self.assertEqual(skipped_ids, self.extraction.selected_source_ids)

        first_source = self.search.sources[0]
        expanded_source = first_source.model_copy(
            update={"task_ids": [*first_source.task_ids, "task-new-scope"]}
        )
        expanded_search = self.search.model_copy(
            update={
                "sources": [expanded_source, *self.search.sources[1:]],
            }
        )
        process_ids, skipped_ids = _search_sources_requiring_extraction(
            expanded_search,
            self.extraction,
        )

        self.assertEqual(process_ids, [first_source.source_id])
        self.assertNotIn(first_source.source_id, skipped_ids)

    def test_merge_updates_preserved_document_task_mappings(self):
        second_task = checker_fixtures.CheckerAgentTests.second_task
        expanded_plan = self.plan.model_copy(
            update={"tasks": [self.plan.tasks[0], second_task]}
        )
        first_source = self.search.sources[0]
        routing_source = self.search.sources[2]
        expanded_sources = [
            first_source.model_copy(
                update={
                    "task_ids": [
                        self.plan.tasks[0].task_id,
                        second_task.task_id,
                    ]
                }
            ),
            self.search.sources[1],
            routing_source.model_copy(
                update={"task_ids": [second_task.task_id]}
            ),
        ]
        merged_search = self.search.model_copy(
            update={
                "selected_task_ids": [
                    self.plan.tasks[0].task_id,
                    second_task.task_id,
                ],
                "unselected_task_ids": [],
                "sources": expanded_sources,
            }
        )
        expanded_documents = [
            *self.extraction.documents[:2],
            self.extraction.documents[2].model_copy(
                update={"task_ids": [second_task.task_id]}
            ),
        ]
        prior_extraction = self.extraction.model_copy(
            update={"documents": expanded_documents}
        )

        merged = ExecutorAgent._merge_extraction(
            expanded_plan,
            merged_search,
            prior_extraction,
            None,
            process_source_ids=[first_source.source_id],
            plan_sha256=checker_fixtures.PLAN_SHA256,
            merged_search_sha256="f" * 64,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            merged_search_reference="/fixtures/sources-r005-free.json",
            prior_extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            prior_extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            resolution=self.resolution,
            resolution_sha256="e" * 64,
            resolution_reference="/fixtures/resolution-r004-free.json",
            execution_mode=ExecutorMode.FREE,
            iteration=5,
        )

        document_by_source = {
            document.source_id: document for document in merged.documents
        }
        self.assertEqual(
            document_by_source[first_source.source_id].task_ids,
            [self.plan.tasks[0].task_id, second_task.task_id],
        )
        second_result = next(
            item
            for item in merged.task_results
            if item.task_id == second_task.task_id
        )
        self.assertIn(first_source.source_id, second_result.source_ids)

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
        self.assertEqual(
            execution.preserved_processed_source_ids,
            [self.search.sources[0].source_id],
        )
        self.assertTrue(
            {claim.claim_id for claim in self.extraction.claims}.issubset(
                {claim.claim_id for claim in merged_extraction.claims}
            )
        )
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

    def test_offline_reconciliation_records_current_lineage_and_adds_no_usage(self):
        merged_search, current, _ = ExecutorAgent(
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

        reconciled = ExecutorAgent.reconcile_extraction(
            self.plan,
            merged_search,
            self.extraction,
            current,
            self.resolution,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            merged_search_sha256=current.search_sha256,
            prior_extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            current_extraction_sha256="f" * 64,
            resolution_sha256="e" * 64,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            merged_search_reference="/fixtures/sources-r005.json",
            prior_extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            current_extraction_reference="/fixtures/extractions-r005.json",
            resolution_reference="/fixtures/resolution-r004.json",
        )

        self.assertEqual(
            reconciled.reconciled_from_extraction_id,
            current.extraction_id,
        )
        self.assertEqual(reconciled.reconciled_from_extraction_sha256, "f" * 64)
        self.assertEqual(reconciled.agent_usage, current.agent_usage)
        self.assertEqual(reconciled.failed_attempts, current.failed_attempts)
        self.assertTrue(
            {claim.claim_id for claim in self.extraction.claims}.issubset(
                {claim.claim_id for claim in reconciled.claims}
            )
        )

    def test_paid_search_batch_uses_resolver_query_and_adds_new_source(self):
        original_item = self.resolution.work_items[0]
        resolver_queries = [
            '"Żabka" "Żabka" aktualny dokument franczyzowy 2026',
            "franchise disclosure relationship law PL PL",
        ]
        normalized_queries = [
            '"Żabka" aktualny dokument franczyzowy 2026',
            "franchise disclosure relationship law PL",
        ]
        changed_item = original_item.model_copy(
            update={
                "allowed_actions": [ResolverAction.SEARCH_NEW_SOURCE],
                "selected_action": ResolverAction.SEARCH_NEW_SOURCE,
                "selected_source_ids": [],
                "queries": resolver_queries,
            }
        )
        original_batch = self.resolution.execution_batches[0]
        changed_batch = original_batch.model_copy(
            update={
                "action": ResolverAction.SEARCH_NEW_SOURCE,
                "source_ids": [],
                "queries": resolver_queries,
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

        self.assertEqual(
            search_llm.calls[0][1][0].search_queries,
            normalized_queries,
        )
        self.assertEqual(
            merged_search.limits.query_overrides,
            {changed_item.task_id: normalized_queries},
        )
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
