import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from datacollector.agents.checker import CheckerAgent
from datacollector.agents.resolver import ResolverAgent, ResolverValidationError
from datacollector.cli import main
from datacollector.llm.protocol import ResolverGeneration, ResolverProviderError
from datacollector.schemas import (
    AgentIterationUsage,
    CheckerClaimDecisionDraft,
    CheckerDraft,
    CheckerIssueCode,
    CheckerModelSemanticFit,
    CheckerModelSourceSupport,
    CheckerModelVerdict,
    CheckerNextAction,
    CheckerFollowUpReason,
    ResearchPlan,
    ResolverAction,
    ResolverDraft,
    ResolverItemDraft,
    ResolverResults,
    ResolverStrategySource,
    SourceType,
    TokenUsage,
)
from datacollector.tests import test_checker as checker_fixtures


CHECK_SHA256 = "d" * 64
CHECK_REFERENCE = "/fixtures/check-r004.json"


class FixtureResolverLLM:
    model_name = "fake-resolver-model"

    def __init__(self, draft_factory=None):
        self.draft_factory = draft_factory
        self.calls = []

    def generate(
        self,
        plan,
        search_results,
        checker_results,
        work_items,
        system_prompt,
        *,
        iteration,
        call_index,
    ):
        self.calls.append(work_items)
        available = {
            source_id
            for item in work_items
            for source_id in [
                *item.selected_source_ids,
                *item.fallback_source_ids,
            ]
        }
        scope_source_ids = [
            source.source_id
            for source in search_results.sources
            if source.source_id in available
        ]
        draft = (
            self.draft_factory(work_items)
            if self.draft_factory is not None
            else ResolverDraft(
                items=[
                    ResolverItemDraft(
                        follow_up_id=item.follow_up_id,
                        selected_action=item.selected_action,
                        selected_source_ids=item.selected_source_ids,
                        sequence=item.sequence,
                        rationale="The fixture retains deterministic routing.",
                    )
                    for item in work_items
                ]
            )
        )
        return ResolverGeneration(
            draft=draft,
            usage=AgentIterationUsage(
                agent="resolver",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=list(
                    dict.fromkeys(item.task_id for item in work_items)
                ),
                scope_source_ids=scope_source_ids,
                requested_model=self.model_name,
                resolved_model=self.model_name,
                tokens=TokenUsage(
                    input_tokens=300,
                    output_tokens=80,
                    total_tokens=380,
                ),
            ),
        )


class FailingResolverLLM(FixtureResolverLLM):
    def generate(self, *args, **kwargs):
        work_items = args[3]
        iteration = kwargs["iteration"]
        raise ResolverProviderError(
            "Fixture failure.",
            code="incomplete_response",
            iteration=iteration,
            call_index=1,
            scope_task_ids=list(
                dict.fromkeys(item.task_id for item in work_items)
            ),
            requested_model=self.model_name,
        )


class ResolverAgentTests(TestCase):
    @classmethod
    def setUpClass(cls):
        checker_fixtures.CheckerAgentTests.setUpClass()
        cls.plan = checker_fixtures.CheckerAgentTests.plan
        cls.search_results = checker_fixtures.CheckerAgentTests.search_results
        cls.extraction_results = checker_fixtures.CheckerAgentTests.extraction_results
        target_field = cls.plan.tasks[0].target_fields[0]

        def mixed_draft(extraction_results):
            target_claims = [
                claim
                for claim in extraction_results.claims
                if claim.target_field == target_field
            ]
            rejected_claim_id = target_claims[0].claim_id
            return CheckerDraft(
                decisions=[
                    CheckerClaimDecisionDraft(
                        claim_id=claim.claim_id,
                        verdict=(
                            CheckerModelVerdict.REJECTED
                            if claim.claim_id == rejected_claim_id
                            else CheckerModelVerdict.ACCEPTED
                        ),
                        semantic_fit=(
                            CheckerModelSemanticFit.MISMATCH
                            if claim.claim_id == rejected_claim_id
                            else CheckerModelSemanticFit.DIRECT
                        ),
                        source_support=CheckerModelSourceSupport.SUFFICIENT,
                        issue_codes=(
                            [CheckerIssueCode.UNSUPPORTED_CLAIM]
                            if claim.claim_id == rejected_claim_id
                            else []
                        ),
                        rationale="Fixture semantic decision for Resolver input.",
                    )
                    for claim in extraction_results.claims
                ]
            )

        cls.checker_results = CheckerAgent(
            checker_fixtures.FixtureCheckerLLM(mixed_draft)
        ).create_check_results(
            cls.plan,
            cls.search_results,
            cls.extraction_results,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            search_sha256=checker_fixtures.SEARCH_SHA256,
            extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            search_reference=checker_fixtures.SEARCH_REFERENCE,
            iteration=4,
        )

    def _run(self, llm=None, *, checker_results=None, **kwargs):
        return ResolverAgent(llm).create_resolution_results(
            self.plan,
            self.search_results,
            self.extraction_results,
            checker_results or self.checker_results,
            plan_sha256=kwargs.pop(
                "plan_sha256", checker_fixtures.PLAN_SHA256
            ),
            search_sha256=kwargs.pop(
                "search_sha256", checker_fixtures.SEARCH_SHA256
            ),
            extraction_sha256=kwargs.pop(
                "extraction_sha256", checker_fixtures.EXTRACTION_SHA256
            ),
            check_sha256=kwargs.pop("check_sha256", CHECK_SHA256),
            check_reference=kwargs.pop("check_reference", CHECK_REFERENCE),
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            search_reference=checker_fixtures.SEARCH_REFERENCE,
            extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            iteration=kwargs.pop("iteration", 4),
            **kwargs,
        )

    def test_free_routes_unresolved_field_without_provider_cost(self):
        results = self._run()

        self.assertEqual(results.generated_by, "deterministic")
        self.assertEqual(
            results.strategy_source,
            ResolverStrategySource.DETERMINISTIC,
        )
        self.assertFalse(results.provider_executed)
        self.assertEqual(results.agent_usage, [])
        self.assertTrue(results.ready_for_execution)
        self.assertEqual(len(results.work_items), 1)
        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.REEXTRACT_EXISTING,
        )
        self.assertEqual(len(results.execution_batches), 1)

    def test_ready_selected_scope_schedules_next_plan_batch(self):
        second_task = checker_fixtures.CheckerAgentTests.second_task
        plan_payload = self.plan.model_dump(mode="python")
        plan_payload["tasks"] = [self.plan.tasks[0], second_task]
        expanded_plan = ResearchPlan.model_validate(plan_payload)
        expanded_search = self.search_results.model_copy(
            update={"unselected_task_ids": [second_task.task_id]}
        )
        checker = CheckerAgent(
            checker_fixtures.FixtureCheckerLLM(
                checker_fixtures.CheckerAgentTests._accepted_draft
            )
        ).create_check_results(
            expanded_plan,
            expanded_search,
            self.extraction_results,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            search_sha256=checker_fixtures.SEARCH_SHA256,
            extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            search_reference=checker_fixtures.SEARCH_REFERENCE,
            iteration=4,
        )

        self.assertTrue(checker.selected_scope_ready)
        self.assertEqual(
            checker.recommended_next_action,
            CheckerNextAction.RESEARCH_NEXT_BATCH,
        )
        results = ResolverAgent().create_resolution_results(
            expanded_plan,
            expanded_search,
            self.extraction_results,
            checker,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            search_sha256=checker_fixtures.SEARCH_SHA256,
            extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            check_sha256=CHECK_SHA256,
            check_reference=CHECK_REFERENCE,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            search_reference=checker_fixtures.SEARCH_REFERENCE,
            extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            iteration=4,
            max_search_tasks=1,
        )

        self.assertEqual(results.search_task_ids, [second_task.task_id])
        self.assertEqual(len(results.work_items), 1)
        self.assertEqual(
            results.work_items[0].reason,
            CheckerFollowUpReason.SCOPE_NOT_STARTED,
        )
        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.SEARCH_NEW_SOURCE,
        )
        self.assertEqual(results.execution_source_ids, [])
        self.assertTrue(
            any("previously unevaluated" in warning for warning in results.warnings)
        )

    def test_ready_selected_scope_processes_known_source_before_new_tasks(self):
        source = checker_fixtures.CheckerAgentTests._make_source(
            4, SourceType.OFFICIAL
        )
        action = self.search_results.actions[0].model_copy(
            update={
                "source_urls": [
                    *self.search_results.actions[0].source_urls,
                    source.canonical_url,
                ]
            }
        )
        task_result = self.search_results.task_results[0].model_copy(
            update={
                "source_ids": [
                    *self.search_results.task_results[0].source_ids,
                    source.source_id,
                ]
            }
        )
        expanded_search = self.search_results.model_copy(
            update={
                "actions": [action],
                "sources": [*self.search_results.sources, source],
                "task_results": [task_result],
            }
        )
        partial_extraction = self.extraction_results.model_copy(
            update={"unselected_source_ids": [source.source_id]}
        )
        checker = CheckerAgent(
            checker_fixtures.FixtureCheckerLLM(
                checker_fixtures.CheckerAgentTests._accepted_draft
            )
        ).create_check_results(
            self.plan,
            expanded_search,
            partial_extraction,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            search_sha256=checker_fixtures.SEARCH_SHA256,
            extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            extraction_reference=checker_fixtures.EXTRACTION_REFERENCE,
            plan_reference=checker_fixtures.PLAN_REFERENCE,
            search_reference=checker_fixtures.SEARCH_REFERENCE,
            iteration=4,
        )

        self.assertTrue(checker.selected_scope_ready)
        self.assertEqual(checker.unevaluated_source_ids, [source.source_id])
        results = ResolverAgent().create_resolution_results(
            self.plan,
            expanded_search,
            partial_extraction,
            checker,
            plan_sha256=checker_fixtures.PLAN_SHA256,
            search_sha256=checker_fixtures.SEARCH_SHA256,
            extraction_sha256=checker_fixtures.EXTRACTION_SHA256,
            check_sha256=CHECK_SHA256,
            check_reference=CHECK_REFERENCE,
            iteration=4,
        )

        self.assertEqual(results.search_task_ids, [])
        self.assertEqual(results.execution_source_ids, [source.source_id])
        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.EXTRACT_KNOWN_SOURCE,
        )
        self.assertEqual(
            results.work_items[0].reason,
            CheckerFollowUpReason.SOURCE_NOT_EVALUATED,
        )

    def test_known_candidate_is_preferred_over_retry_and_reextraction(self):
        follow_up = self.checker_results.follow_up_tasks[0]
        changed_follow_up = follow_up.model_copy(
            update={
                "candidate_source_ids": [self.search_results.sources[1].source_id],
                "retry_source_ids": [self.search_results.sources[0].source_id],
                "reextract_source_ids": [self.search_results.sources[2].source_id],
            }
        )
        changed_checker = self.checker_results.model_copy(
            update={"follow_up_tasks": [changed_follow_up]}
        )

        results = self._run(checker_results=changed_checker)

        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.EXTRACT_KNOWN_SOURCE,
        )
        self.assertEqual(
            results.work_items[0].selected_source_ids,
            [self.search_results.sources[1].source_id],
        )

    def test_document_mention_uses_retry_not_reextraction(self):
        follow_up = self.checker_results.follow_up_tasks[0]
        changed_follow_up = follow_up.model_copy(
            update={
                "candidate_source_ids": [],
                "retry_source_ids": [self.search_results.sources[0].source_id],
                "reextract_source_ids": [self.search_results.sources[1].source_id],
            }
        )
        task_result = self.checker_results.task_results[0]
        changed_field = task_result.field_results[0].model_copy(
            update={"issue_codes": [CheckerIssueCode.MENTIONED_NOT_OBTAINED]}
        )
        changed_task = task_result.model_copy(
            update={
                "field_results": [
                    changed_field,
                    *task_result.field_results[1:],
                ]
            }
        )
        changed_checker = self.checker_results.model_copy(
            update={
                "follow_up_tasks": [changed_follow_up],
                "task_results": [changed_task],
            }
        )

        results = self._run(checker_results=changed_checker)

        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.RETRY_RETRIEVAL,
        )
        self.assertNotIn(
            ResolverAction.REEXTRACT_EXISTING,
            results.work_items[0].allowed_actions,
        )

    def test_paid_strategy_is_grounded_and_usage_is_recorded(self):
        llm = FixtureResolverLLM()

        results = self._run(llm)

        self.assertEqual(results.generated_by, "openai")
        self.assertEqual(results.strategy_source, ResolverStrategySource.OPENAI)
        self.assertTrue(results.provider_executed)
        self.assertEqual(len(results.agent_usage), 1)
        self.assertEqual(results.agent_usage[0].agent, "resolver")
        self.assertEqual(results.failed_attempts, [])

    def test_usage_scope_allows_paid_work_item_reordering(self):
        base = self._run(FixtureResolverLLM())
        original = base.work_items[0].model_copy(update={"sequence": 2})
        reordered = base.work_items[0].model_copy(
            update={
                "resolution_item_id": "resolution-item-aaaaaaaaaaaaaaaa",
                "follow_up_id": "followup-bbbbbbbbbbbbbbbb",
                "task_id": "task-second-fixture",
                "sequence": 1,
            }
        )
        work_items = [reordered, original]
        usage = base.agent_usage[0].model_copy(
            update={
                "scope_task_ids": [original.task_id, reordered.task_id],
            }
        )
        payload = base.model_dump(mode="python")
        payload.update(
            {
                "selected_follow_up_ids": [
                    item.follow_up_id for item in work_items
                ],
                "work_items": work_items,
                "execution_batches": ResolverAgent._build_batches(work_items),
                "execution_source_ids": list(
                    dict.fromkeys(
                        source_id
                        for item in work_items
                        for source_id in item.selected_source_ids
                    )
                ),
                "agent_usage": [usage],
            }
        )

        validated = ResolverResults.model_validate(payload)

        self.assertEqual(
            validated.agent_usage[0].scope_task_ids,
            [original.task_id, reordered.task_id],
        )
        self.assertEqual(
            [item.task_id for item in validated.work_items],
            [reordered.task_id, original.task_id],
        )

    def test_paid_failure_retains_deterministic_executable_fallback(self):
        results = self._run(FailingResolverLLM())

        self.assertEqual(results.generated_by, "openai")
        self.assertEqual(
            results.strategy_source,
            ResolverStrategySource.DETERMINISTIC_FALLBACK,
        )
        self.assertTrue(results.ready_for_execution)
        self.assertEqual(len(results.failed_attempts), 1)
        self.assertTrue(results.failed_attempts[0].token_usage_unknown)

    def test_invalid_paid_source_selection_is_discarded_with_usage(self):
        def invalid_draft(work_items):
            item = work_items[0]
            return ResolverDraft(
                items=[
                    ResolverItemDraft(
                        follow_up_id=item.follow_up_id,
                        selected_action=item.selected_action,
                        selected_source_ids=["source-ffffffffffffffff"],
                        sequence=1,
                        rationale="The fixture deliberately invents a source ID.",
                    )
                ]
            )

        results = self._run(FixtureResolverLLM(invalid_draft))

        self.assertEqual(
            results.strategy_source,
            ResolverStrategySource.DETERMINISTIC_FALLBACK,
        )
        self.assertEqual(len(results.agent_usage), 1)
        self.assertEqual(len(results.failed_attempts), 1)
        self.assertEqual(
            results.failed_attempts[0].error_code,
            "invalid_resolver_output",
        )
        self.assertNotIn(
            "source-ffffffffffffffff",
            results.execution_source_ids,
        )

    def test_rejects_broken_checker_lineage(self):
        with self.assertRaisesRegex(
            ResolverValidationError,
            "Checker artifact SHA-256",
        ):
            self._run(check_sha256="invalid")

    def test_free_cli_writes_free_resolution_summary(self):
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            plan_path = (directory / "plan.json").resolve()
            search_path = (directory / "sources-r003.json").resolve()
            extraction_path = (directory / "extractions-r004.json").resolve()
            check_path = (directory / "check-r004.json").resolve()
            checker = self.checker_results.model_copy(
                update={
                    "plan_reference": str(plan_path),
                    "search_reference": str(search_path),
                    "extraction_reference": str(extraction_path),
                }
            )
            expected_resolution_path = directory / "resolution-r004-free.json"
            output = StringIO()
            with (
                patch(
                    "datacollector.cli.load_checker_results",
                    return_value=(checker, CHECK_SHA256),
                ),
                patch(
                    "datacollector.cli.load_extraction_results",
                    return_value=(
                        self.extraction_results,
                        checker_fixtures.EXTRACTION_SHA256,
                    ),
                ),
                patch(
                    "datacollector.cli.load_search_results",
                    return_value=(
                        self.search_results,
                        checker_fixtures.SEARCH_SHA256,
                    ),
                ),
                patch(
                    "datacollector.cli.load_research_plan",
                    return_value=(self.plan, checker_fixtures.PLAN_SHA256),
                ),
                patch(
                    "datacollector.cli.save_resolver_results",
                    return_value=expected_resolution_path,
                ),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "resolve",
                        "--check",
                        str(check_path),
                        "--plan",
                        str(plan_path),
                        "--sources",
                        str(search_path),
                        "--extractions",
                        str(extraction_path),
                        "--free",
                        "--iteration",
                        "4",
                    ]
                )

            summary = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["generated_by"], "deterministic")
            self.assertEqual(summary["strategy_source"], "deterministic")
            self.assertEqual(summary["usage_totals"]["total_tokens"], 0)
            self.assertEqual(
                summary["resolution_path"],
                str(expected_resolution_path),
            )
