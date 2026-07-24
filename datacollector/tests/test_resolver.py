import hashlib
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from datacollector.agents.checker import CheckerAgent
from datacollector.agents.planner import PlannerAgent
from datacollector.agents.resolver import ResolverAgent, ResolverValidationError
from datacollector.catalog import load_question_catalog
from datacollector.cli import main
from datacollector.llm.protocol import ResolverGeneration, ResolverProviderError
from datacollector.profiles import load_profile_catalog
from datacollector.schemas import (
    AgentIterationUsage,
    CheckerClaimDecisionDraft,
    CheckerDraft,
    CheckerFollowUpAction,
    CheckerFollowUpRoute,
    CheckerFollowUpTask,
    CheckerIssueCode,
    CheckerModelSemanticFit,
    CheckerModelSourceSupport,
    CheckerModelVerdict,
    CheckerNextAction,
    CheckerFollowUpReason,
    DocumentParseStatus,
    DocumentRetrievalStatus,
    ExtractionSemanticScope,
    FieldAvailability,
    PlannerInput,
    ProfileReuseScope,
    ResearchPlan,
    ResolverAction,
    ResolverDraft,
    ResolverItemDraft,
    ResolverNextAction,
    ResolverResults,
    ResolverStrategySource,
    SourceType,
    TokenUsage,
)
from datacollector.tests import test_checker as checker_fixtures


CHECK_SHA256 = "d" * 64
CHECK_REFERENCE = "/fixtures/check-r004.json"


def plan_with_local_quality_task(base_plan):
    complete_plan = PlannerAgent(load_question_catalog()).create_plan(
        PlannerInput(
            brand_name="Example",
            target_country="PL",
            depth="catalog",
        )
    )
    quality_task = next(
        task for task in complete_plan.tasks if task.section_id == "data_quality"
    ).model_copy(update={"depends_on": []})
    plan_payload = base_plan.model_dump(mode="python")
    plan_payload["tasks"] = [base_plan.tasks[0], quality_task]
    plan_payload["critical_fields"] = [
        field
        for task in plan_payload["tasks"]
        if task.priority.value == "critical"
        for field in task.target_fields
    ]
    return ResearchPlan.model_validate(plan_payload), quality_task


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
        cls.profile_plan = PlannerAgent(
            load_question_catalog(),
            profile_catalog=load_profile_catalog(),
        ).create_plan(
            PlannerInput(
                brand_name="Example",
                target_country="PL",
                profile_id="PL:L3",
            )
        )

    def _run(self, llm=None, *, checker_results=None, **kwargs):
        search_results = kwargs.pop("search_results", self.search_results)
        extraction_results = kwargs.pop(
            "extraction_results", self.extraction_results
        )
        return ResolverAgent(llm).create_resolution_results(
            self.plan,
            search_results,
            extraction_results,
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

    @classmethod
    def _profile_field(cls, availability):
        task_by_question = {
            task.catalog_question_id: task for task in cls.profile_plan.tasks
        }
        for question in cls.profile_plan.profile_snapshot.questions:
            for field in question.fields:
                if field.availability == availability:
                    return (
                        task_by_question[question.question_id],
                        question,
                        field,
                    )
        raise AssertionError(f"No profile field for {availability}")

    @staticmethod
    def _profile_follow_up(task, question, field, *, route=None):
        return CheckerFollowUpTask(
            follow_up_id=(
                "followup-"
                + hashlib.sha256(
                    f"{task.task_id}:{field.target_field}".encode()
                ).hexdigest()[:16]
            ),
            task_id=task.task_id,
            target_field=field.target_field,
            availability=field.availability,
            required_for_completion=field.required_for_completion,
            reuse_scope=question.reuse_scope,
            priority=task.priority,
            reason=CheckerFollowUpReason.MISSING_CLAIM,
            question=f"Resolve profile field {field.target_field} from valid evidence.",
            required_source_types=task.preferred_source_types,
            route=route or CheckerFollowUpRoute.RESOLVER,
            action=CheckerFollowUpAction.FIND_ALTERNATIVE_SOURCE,
            minimum_additional_sources=1,
            suggested_queries=task.search_queries[:3],
            completion_criteria=(
                f"Complete when field {field.target_field} has valid evidence."
            ),
        )

    def _build_profile_items(self, follow_ups):
        return ResolverAgent._build_deterministic_items(
            self.profile_plan,
            follow_ups,
            self.checker_results,
            eligible_source_pools={
                follow_up.follow_up_id: {
                    ResolverAction.EXTRACT_KNOWN_SOURCE: [],
                    ResolverAction.RETRY_RETRIEVAL: [],
                    ResolverAction.REEXTRACT_EXISTING: [],
                }
                for follow_up in follow_ups
            },
            max_source_actions=10,
            max_search_tasks=5,
            max_queries_per_item=3,
        )

    def test_private_and_manual_profile_fields_are_locked_to_human_review(self):
        follow_ups = []
        for availability in (
            FieldAvailability.PRIVATE_DOCUMENT_REQUIRED,
            FieldAvailability.MANUAL_RESEARCH_REQUIRED,
            FieldAvailability.CONFIDENTIAL_DEAL_ROOM,
        ):
            try:
                task, question, field = self._profile_field(availability)
            except AssertionError:
                # PL:L3 currently has no confidential deal-room field, but the
                # boundary remains enforced directly by the schema test below.
                continue
            follow_ups.append(self._profile_follow_up(task, question, field))

        items = self._build_profile_items(follow_ups)

        self.assertTrue(items)
        for item in items:
            self.assertEqual(item.allowed_actions, [ResolverAction.HUMAN_REVIEW])
            self.assertEqual(item.selected_action, ResolverAction.HUMAN_REVIEW)
            self.assertEqual(item.queries, [])
            self.assertEqual(item.selected_source_ids, [])
            self.assertEqual(item.fallback_source_ids, [])
            self.assertIsNotNone(item.required_for_completion)
            self.assertIsNotNone(item.reuse_scope)

    def test_system_derived_profile_field_is_locked_to_local_audit(self):
        task, question, field = self._profile_field(
            FieldAvailability.SYSTEM_DERIVED
        )
        follow_up = self._profile_follow_up(task, question, field)

        item = self._build_profile_items([follow_up])[0]

        self.assertEqual(item.allowed_actions, [ResolverAction.LOCAL_AUDIT])
        self.assertEqual(item.selected_action, ResolverAction.LOCAL_AUDIT)
        self.assertEqual(item.queries, [])
        self.assertEqual(item.minimum_additional_sources, 0)

    def test_profile_follow_ups_rank_required_auto_before_optional_and_human(self):
        follow_ups = {}
        for availability in (
            FieldAvailability.PUBLIC_EXPECTED,
            FieldAvailability.PUBLIC_OPTIONAL,
            FieldAvailability.PRIVATE_DOCUMENT_REQUIRED,
        ):
            task, question, field = self._profile_field(availability)
            follow_ups[availability] = self._profile_follow_up(
                task, question, field
            )

        ranks = {
            availability: ResolverAgent._follow_up_route_rank(
                self.profile_plan, follow_up
            )
            for availability, follow_up in follow_ups.items()
        }

        self.assertLess(
            ranks[FieldAvailability.PUBLIC_EXPECTED],
            ranks[FieldAvailability.PUBLIC_OPTIONAL],
        )
        self.assertLess(
            ranks[FieldAvailability.PUBLIC_OPTIONAL],
            ranks[FieldAvailability.PRIVATE_DOCUMENT_REQUIRED],
        )

    def test_profile_scope_expansion_routes_mixed_and_human_tasks_safely(self):
        snapshot_by_question = {
            question.question_id: question
            for question in self.profile_plan.profile_snapshot.questions
        }

        def availability_set(task):
            return {
                field.availability
                for field in snapshot_by_question[task.catalog_question_id].fields
            }

        auto = {
            FieldAvailability.PUBLIC_EXPECTED,
            FieldAvailability.PUBLIC_OPTIONAL,
            FieldAvailability.REGISTRY_EXPECTED,
        }
        human = {
            FieldAvailability.MANUAL_RESEARCH_REQUIRED,
            FieldAvailability.PRIVATE_DOCUMENT_REQUIRED,
            FieldAvailability.CONFIDENTIAL_DEAL_ROOM,
        }
        mixed_task = next(
            task
            for task in self.profile_plan.tasks
            if availability_set(task) & auto and availability_set(task) & human
        )
        human_task = next(
            task
            for task in self.profile_plan.tasks
            if availability_set(task)
            and availability_set(task).issubset(human)
        )
        system_task = next(
            task
            for task in self.profile_plan.tasks
            if FieldAvailability.SYSTEM_DERIVED in availability_set(task)
            and not availability_set(task) & auto
        )
        checker = self.checker_results.model_copy(
            update={
                "unevaluated_source_ids": [],
                "unevaluated_task_ids": [
                    mixed_task.task_id,
                    human_task.task_id,
                    system_task.task_id,
                ],
            }
        )

        follow_ups = ResolverAgent._build_scope_expansion_follow_ups(
            self.profile_plan,
            self.search_results,
            checker,
        )
        items = self._build_profile_items(follow_ups)
        by_task = {item.task_id: item for item in items}

        self.assertEqual(
            by_task[mixed_task.task_id].selected_action,
            ResolverAction.SEARCH_NEW_SOURCE,
        )
        self.assertIn(
            by_task[mixed_task.task_id].field_availability,
            auto,
        )
        self.assertEqual(
            by_task[human_task.task_id].selected_action,
            ResolverAction.HUMAN_REVIEW,
        )
        self.assertEqual(by_task[human_task.task_id].queries, [])
        self.assertEqual(
            by_task[system_task.task_id].selected_action,
            ResolverAction.LOCAL_AUDIT,
        )

    def test_profile_scope_expansion_does_not_replay_source_backlog_first(self):
        next_task = self.profile_plan.tasks[1]
        checker = self.checker_results.model_copy(
            update={
                "unevaluated_source_ids": [
                    self.search_results.sources[0].source_id
                ],
                "unevaluated_task_ids": [next_task.task_id],
            }
        )

        follow_ups = ResolverAgent._build_scope_expansion_follow_ups(
            self.profile_plan,
            self.search_results,
            checker,
        )

        self.assertEqual(len(follow_ups), 1)
        self.assertEqual(follow_ups[0].task_id, next_task.task_id)
        self.assertEqual(follow_ups[0].target_field, "__task_scope__")
        self.assertEqual(follow_ups[0].candidate_source_ids, [])

    def test_human_only_resolution_skips_paid_resolver_and_is_not_executable(self):
        follow_up = self.checker_results.follow_up_tasks[0].model_copy(
            update={
                "route": CheckerFollowUpRoute.HUMAN_REVIEW,
                "candidate_source_ids": [],
                "retry_source_ids": [],
                "reextract_source_ids": [],
            }
        )
        checker = self.checker_results.model_copy(
            update={"follow_up_tasks": [follow_up]}
        )
        llm = FixtureResolverLLM()

        results = self._run(llm, checker_results=checker)

        self.assertEqual(llm.calls, [])
        self.assertEqual(results.generated_by, "deterministic")
        self.assertFalse(results.provider_executed)
        self.assertFalse(results.ready_for_execution)
        self.assertEqual(
            results.recommended_next_action,
            ResolverNextAction.HUMAN_REVIEW,
        )
        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.HUMAN_REVIEW,
        )
        self.assertEqual(results.work_items[0].queries, [])

    def test_human_plus_field_local_audit_does_not_schedule_executor(self):
        base = self.checker_results.follow_up_tasks[0]
        target_fields = self.plan.tasks[0].target_fields
        local_follow_up = base.model_copy(
            update={
                "follow_up_id": "followup-1111111111111111",
                "target_field": target_fields[0],
                "availability": FieldAvailability.SYSTEM_DERIVED,
                "required_for_completion": False,
                "reuse_scope": ProfileReuseScope.BRAND,
                "route": CheckerFollowUpRoute.RESOLVER,
                "action": CheckerFollowUpAction.LOCAL_AUDIT,
                "reason": CheckerFollowUpReason.MISSING_CLAIM,
                "candidate_source_ids": [],
                "retry_source_ids": [],
                "reextract_source_ids": [],
                "minimum_additional_sources": 0,
                "requires_independent_source": False,
                "suggested_queries": [],
            }
        )
        human_follow_up = base.model_copy(
            update={
                "follow_up_id": "followup-2222222222222222",
                "target_field": target_fields[-1],
                "availability": FieldAvailability.MANUAL_RESEARCH_REQUIRED,
                "required_for_completion": False,
                "reuse_scope": ProfileReuseScope.BRAND,
                "route": CheckerFollowUpRoute.HUMAN_REVIEW,
                "action": CheckerFollowUpAction.MANUAL_RESEARCH,
                "reason": CheckerFollowUpReason.MISSING_CLAIM,
                "candidate_source_ids": [],
                "retry_source_ids": [],
                "reextract_source_ids": [],
                "minimum_additional_sources": 0,
                "requires_independent_source": False,
                "suggested_queries": [],
            }
        )
        checker = self.checker_results.model_copy(
            update={"follow_up_tasks": [local_follow_up, human_follow_up]}
        )
        llm = FixtureResolverLLM()

        results = self._run(llm, checker_results=checker)

        self.assertEqual(llm.calls, [])
        self.assertFalse(results.ready_for_execution)
        self.assertEqual(
            results.recommended_next_action,
            ResolverNextAction.HUMAN_REVIEW,
        )
        self.assertEqual(
            {item.selected_action for item in results.work_items},
            {ResolverAction.LOCAL_AUDIT, ResolverAction.HUMAN_REVIEW},
        )

    def test_resolver_schema_rejects_private_web_search(self):
        task, question, field = self._profile_field(
            FieldAvailability.PRIVATE_DOCUMENT_REQUIRED
        )
        follow_up = self._profile_follow_up(task, question, field)
        item = self._build_profile_items([follow_up])[0]
        payload = item.model_dump(mode="python")
        payload.update(
            {
                "allowed_actions": [ResolverAction.SEARCH_NEW_SOURCE],
                "selected_action": ResolverAction.SEARCH_NEW_SOURCE,
                "queries": ["forbidden private-field web search"],
            }
        )

        with self.assertRaisesRegex(ValueError, "must remain human-only"):
            type(item).model_validate(payload)

    def test_free_reextracts_only_usable_unprocessed_evidence_sources(self):
        follow_up = self.checker_results.follow_up_tasks[0].model_copy(
            update={"minimum_additional_sources": 0}
        )
        task_result = self.checker_results.task_results[0]
        field_result = task_result.field_results[0].model_copy(
            update={"issue_codes": [CheckerIssueCode.UNSUPPORTED_CLAIM]}
        )
        changed_task_result = task_result.model_copy(
            update={
                "field_results": [
                    field_result,
                    *task_result.field_results[1:],
                ]
            }
        )
        checker = self.checker_results.model_copy(
            update={
                "follow_up_tasks": [follow_up],
                "task_results": [changed_task_result],
            }
        )
        results = self._run(checker_results=checker)

        self.assertEqual(results.generated_by, "deterministic")
        self.assertEqual(results.prompt_version, "resolver-system-v2")
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
        self.assertEqual(
            results.work_items[0].selected_source_ids,
            [
                self.search_results.sources[0].source_id,
                self.search_results.sources[1].source_id,
            ],
        )
        self.assertNotIn(
            self.search_results.sources[2].source_id,
            results.available_source_ids,
        )
        self.assertEqual(len(results.execution_batches), 1)

    def test_processed_scopes_force_new_search_instead_of_reextraction(self):
        follow_up = self.checker_results.follow_up_tasks[0].model_copy(
            update={"minimum_additional_sources": 0}
        )
        task_result = self.checker_results.task_results[0]
        field_result = task_result.field_results[0].model_copy(
            update={"issue_codes": [CheckerIssueCode.UNSUPPORTED_CLAIM]}
        )
        changed_task_result = task_result.model_copy(
            update={
                "field_results": [
                    field_result,
                    *task_result.field_results[1:],
                ]
            }
        )
        checker = self.checker_results.model_copy(
            update={
                "follow_up_tasks": [follow_up],
                "task_results": [changed_task_result],
            }
        )
        processed_scopes = [
            ExtractionSemanticScope(
                task_id=follow_up.task_id,
                source_id=source.source_id,
            )
            for source in self.search_results.sources[:2]
        ]
        extraction = self.extraction_results.model_copy(
            update={"semantically_processed_scopes": processed_scopes}
        )

        results = self._run(
            checker_results=checker,
            extraction_results=extraction,
        )

        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.SEARCH_NEW_SOURCE,
        )
        self.assertNotIn(
            ResolverAction.REEXTRACT_EXISTING,
            results.work_items[0].allowed_actions,
        )
        self.assertEqual(results.execution_source_ids, [])

    def test_inaccessible_document_is_not_eligible_for_reextraction(self):
        follow_up = self.checker_results.follow_up_tasks[0].model_copy(
            update={
                "candidate_source_ids": [],
                "retry_source_ids": [
                    self.search_results.sources[0].source_id
                ],
                "reextract_source_ids": [
                    self.search_results.sources[0].source_id
                ],
            }
        )
        inaccessible = self.extraction_results.documents[0].model_copy(
            update={
                "retrieval_status": DocumentRetrievalStatus.NOT_ACCESSIBLE,
                "parse_status": DocumentParseStatus.NOT_ATTEMPTED,
                "error_code": "anti_bot_page",
            }
        )
        extraction = self.extraction_results.model_copy(
            update={
                "documents": [
                    inaccessible,
                    *self.extraction_results.documents[1:],
                ]
            }
        )

        pools = ResolverAgent._eligible_source_pools(
            [follow_up],
            self.search_results,
            extraction,
        )

        self.assertEqual(
            pools[follow_up.follow_up_id][
                ResolverAction.REEXTRACT_EXISTING
            ],
            [],
        )
        self.assertEqual(
            pools[follow_up.follow_up_id][
                ResolverAction.RETRY_RETRIEVAL
            ],
            [],
        )

    def test_materialized_candidate_is_not_scheduled_as_known_source(self):
        source_id = self.search_results.sources[0].source_id
        follow_up = self.checker_results.follow_up_tasks[0].model_copy(
            update={
                "candidate_source_ids": [source_id],
                "retry_source_ids": [],
                "reextract_source_ids": [],
                "minimum_additional_sources": 1,
            }
        )
        checker = self.checker_results.model_copy(
            update={"follow_up_tasks": [follow_up]}
        )

        results = self._run(checker_results=checker)

        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.SEARCH_NEW_SOURCE,
        )
        self.assertNotIn(
            ResolverAction.EXTRACT_KNOWN_SOURCE,
            results.work_items[0].allowed_actions,
        )

    def test_explicit_l1_gap_policy_prefers_bounded_new_search(self):
        follow_up = self.checker_results.follow_up_tasks[0]
        changed_checker = self.checker_results.model_copy(
            update={"follow_up_tasks": [follow_up]}
        )

        results = self._run(
            checker_results=changed_checker,
            prefer_new_search=True,
        )

        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.SEARCH_NEW_SOURCE,
        )
        self.assertEqual(results.search_task_ids, [follow_up.task_id])
        self.assertIn(
            "explicitly preferred bounded new-source search",
            " ".join(results.warnings),
        )

    def test_corroboration_prefers_new_search_over_reextraction(self):
        follow_up = self.checker_results.follow_up_tasks[0].model_copy(
            update={
                "action": CheckerFollowUpAction.CORROBORATE,
                "reason": CheckerFollowUpReason.NEEDS_CORROBORATION,
                "minimum_additional_sources": 1,
                "requires_independent_source": True,
            }
        )
        checker = self.checker_results.model_copy(
            update={"follow_up_tasks": [follow_up]}
        )

        results = self._run(checker_results=checker)

        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.SEARCH_NEW_SOURCE,
        )
        self.assertEqual(results.execution_source_ids, [])

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

    def test_exhausted_repairs_can_explicitly_advance_with_documented_gaps(self):
        second_task = checker_fixtures.CheckerAgentTests.second_task
        plan_payload = self.plan.model_dump(mode="python")
        plan_payload["tasks"] = [self.plan.tasks[0], second_task]
        expanded_plan = ResearchPlan.model_validate(plan_payload)
        expanded_search = self.search_results.model_copy(
            update={"unselected_task_ids": [second_task.task_id]}
        )
        checker = self.checker_results.model_copy(
            update={
                "unevaluated_task_ids": [second_task.task_id],
                "scope_complete": False,
            }
        )

        llm = FixtureResolverLLM()
        results = ResolverAgent(llm).create_resolution_results(
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
            completed_gap_rounds=expanded_plan.stop_conditions.max_rounds,
            force_scope_expansion=True,
        )

        self.assertTrue(results.scope_expansion_override)
        self.assertEqual(results.strategy_source, ResolverStrategySource.OPENAI)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(results.search_task_ids, [second_task.task_id])
        self.assertEqual(
            results.work_items[0].reason,
            CheckerFollowUpReason.SCOPE_NOT_STARTED,
        )
        self.assertTrue(
            any("unresolved selected-scope gaps" in item for item in results.warnings)
        )

    def test_local_quality_scope_skips_paid_resolver_and_web_search(self):
        expanded_plan, quality_task = plan_with_local_quality_task(self.plan)
        expanded_search = self.search_results.model_copy(
            update={"unselected_task_ids": [quality_task.task_id]}
        )
        checker = self.checker_results.model_copy(
            update={
                "unevaluated_task_ids": [quality_task.task_id],
                "scope_complete": False,
            }
        )
        llm = FixtureResolverLLM()

        results = ResolverAgent(llm).create_resolution_results(
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
            completed_gap_rounds=expanded_plan.stop_conditions.max_rounds,
            force_scope_expansion=True,
        )

        self.assertEqual(llm.calls, [])
        self.assertEqual(results.generated_by, "deterministic")
        self.assertFalse(results.provider_executed)
        self.assertEqual(results.agent_usage, [])
        self.assertEqual(results.search_task_ids, [])
        self.assertEqual(results.execution_source_ids, [])
        self.assertEqual(len(results.work_items), 1)
        self.assertTrue(results.ready_for_execution)
        self.assertEqual(
            results.recommended_next_action,
            ResolverNextAction.EXECUTE_RESOLUTION,
        )
        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.LOCAL_AUDIT,
        )
        self.assertEqual(results.work_items[0].queries, [])
        self.assertTrue(
            any("skipped its provider call" in item for item in results.warnings)
        )

    def test_scope_expansion_override_is_rejected_before_repair_limit(self):
        second_task = checker_fixtures.CheckerAgentTests.second_task
        plan_payload = self.plan.model_dump(mode="python")
        plan_payload["tasks"] = [self.plan.tasks[0], second_task]
        expanded_plan = ResearchPlan.model_validate(plan_payload)
        expanded_search = self.search_results.model_copy(
            update={"unselected_task_ids": [second_task.task_id]}
        )
        checker = self.checker_results.model_copy(
            update={
                "unevaluated_task_ids": [second_task.task_id],
                "scope_complete": False,
            }
        )
        with self.assertRaisesRegex(
            ResolverValidationError,
            "after the Planner repair-round limit",
        ):
            ResolverAgent().create_resolution_results(
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
                completed_gap_rounds=0,
                force_scope_expansion=True,
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
        candidate = self.search_results.sources[1].model_copy(
            update={
                "source_id": "source-abcdef0123456789",
                "url": "https://example.com/new-candidate",
                "canonical_url": "https://example.com/new-candidate",
            }
        )
        expanded_search = self.search_results.model_copy(
            update={"sources": [*self.search_results.sources, candidate]}
        )
        changed_follow_up = follow_up.model_copy(
            update={
                "candidate_source_ids": [candidate.source_id],
                "retry_source_ids": [self.search_results.sources[0].source_id],
                "reextract_source_ids": [self.search_results.sources[2].source_id],
            }
        )
        changed_checker = self.checker_results.model_copy(
            update={"follow_up_tasks": [changed_follow_up]}
        )

        results = self._run(
            checker_results=changed_checker,
            search_results=expanded_search,
        )

        self.assertEqual(
            results.work_items[0].selected_action,
            ResolverAction.EXTRACT_KNOWN_SOURCE,
        )
        self.assertEqual(
            results.work_items[0].selected_source_ids,
            [candidate.source_id],
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
        transient_document = self.extraction_results.documents[0].model_copy(
            update={
                "retrieval_status": DocumentRetrievalStatus.FAILED,
                "parse_status": DocumentParseStatus.NOT_ATTEMPTED,
                "error_code": "tls_error",
            }
        )
        transient_extraction = self.extraction_results.model_copy(
            update={
                "documents": [
                    transient_document,
                    *self.extraction_results.documents[1:],
                ]
            }
        )

        results = self._run(
            checker_results=changed_checker,
            extraction_results=transient_extraction,
        )

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
                "search_task_ids": list(
                    dict.fromkeys(
                        item.task_id
                        for item in work_items
                        if item.selected_action
                        == ResolverAction.SEARCH_NEW_SOURCE
                    )
                ),
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
                        selected_action=ResolverAction.REEXTRACT_EXISTING,
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
            "invalid_source_selection",
        )
        self.assertTrue(
            any(
                "invalid_source_selection" in warning
                for warning in results.warnings
            )
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

    def test_gap_repair_round_limit_blocks_provider_before_call(self):
        llm = FixtureResolverLLM()

        with self.assertRaisesRegex(
            ResolverValidationError,
            "gap-repair limit reached",
        ):
            self._run(
                llm,
                completed_gap_rounds=self.plan.stop_conditions.max_rounds,
            )

        self.assertEqual(llm.calls, [])

    def test_gap_repair_round_limit_requires_explicit_override(self):
        results = self._run(
            completed_gap_rounds=self.plan.stop_conditions.max_rounds,
            allow_round_limit=True,
        )

        self.assertTrue(
            any("round-limit override" in warning for warning in results.warnings)
        )

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
