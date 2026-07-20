import hashlib
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from datacollector.agents.checker import (
    CheckerAgent,
    CheckerValidationError,
    _document_is_retryable,
)
from datacollector.agents.extractor import ExtractorAgent
from datacollector.agents.planner import PlannerAgent
from datacollector.catalog import load_question_catalog
from datacollector.profiles import load_profile_catalog
from datacollector.documents import FetchedDocument, FetchStatus
from datacollector.llm.pricing import build_web_search_tool_usage
from datacollector.llm.protocol import (
    CheckerGeneration,
    CheckerProviderError,
    ExtractorGeneration,
)
from datacollector.schemas import (
    AgentIterationUsage,
    CheckerClaimDecisionDraft,
    CheckerContradictionDraft,
    CheckerContradictionKind,
    CheckerDraft,
    CheckerFieldStatus,
    CheckerFollowUpAction,
    CheckerFollowUpReason,
    CheckerFollowUpRoute,
    CheckerIssueCode,
    CheckerMode,
    CheckerModelSemanticFit,
    CheckerModelSourceSupport,
    CheckerModelVerdict,
    CheckerNextAction,
    CheckerSeverity,
    CheckerScoreBreakdown,
    CheckerSemanticFit,
    CheckerUnsafeCategory,
    CheckerUnsafeItemDraft,
    CheckerVerdict,
    DocumentRetrievalStatus,
    FieldAvailability,
    ExtractionConfidence,
    ExtractorClaimDraft,
    ExtractorDraft,
    PlannerInput,
    ProfileReuseScope,
    RawExtractionClaim,
    ResearchPlan,
    CheckerResults,
    SearchAction,
    SearchLimits,
    SearchQueryCoverage,
    SearchResults,
    SearchSource,
    SearchSourceOrigin,
    SearchTaskResult,
    SearchTaskStatus,
    SourceAuthorityClass,
    SourceIndependence,
    SourceType,
    TokenUsage,
)


NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
PLAN_SHA256 = "a" * 64
SEARCH_SHA256 = "b" * 64
EXTRACTION_SHA256 = "c" * 64
PLAN_REFERENCE = "/fixtures/plan.json"
SEARCH_REFERENCE = "/fixtures/sources.json"
EXTRACTION_REFERENCE = "/fixtures/extractions.json"
EXACT_VALUE = "Example Polska sp. z o.o."
EXACT_QUOTE = (
    "Example Polska sp. z o.o. states the brand name, aliases, franchisor legal "
    "name, registration ID, parent entities, official website, and franchise "
    "offer website in this source for the brand identity research task."
)


def _checker_usage(*, iteration, task_ids, source_ids):
    return AgentIterationUsage(
        agent="checker",
        iteration=iteration,
        call_index=1,
        scope_task_ids=task_ids,
        scope_source_ids=source_ids,
        requested_model="fake-checker-model",
        resolved_model="fake-checker-model",
        response_id="resp-checker-1",
        tokens=TokenUsage(
            input_tokens=500,
            output_tokens=100,
            reasoning_tokens=20,
            total_tokens=600,
        ),
    )


class FixtureFetcher:
    def __init__(self):
        self.calls = []

    def fetch(self, url, *, source_id=""):
        self.calls.append((url, source_id))
        content = EXACT_QUOTE.encode("utf-8")
        return FetchedDocument(
            source_id=source_id,
            requested_url=url,
            final_url=url,
            status=FetchStatus.FETCHED,
            fetched_at=NOW,
            http_status=200,
            media_type="text/html",
            content=content,
            text=EXACT_QUOTE,
            title="Fixture source",
            byte_count=len(content),
            content_sha256=hashlib.sha256(content).hexdigest(),
            text_sha256=hashlib.sha256(content).hexdigest(),
        )


class FixtureExtractorLLM:
    model_name = "fake-extractor-model"

    def generate(
        self,
        plan,
        source,
        document,
        tasks,
        passages,
        system_prompt,
        *,
        iteration,
        call_index,
    ):
        del plan, document, system_prompt
        task = next(task for task in tasks if task.task_id in source.task_ids)
        passage = next(
            passage for passage in passages if passage.task_id == task.task_id
        )
        return ExtractorGeneration(
            draft=ExtractorDraft(
                claims=[
                    ExtractorClaimDraft(
                        task_id=task.task_id,
                        target_field=target_field,
                        passage_id=passage.passage_id,
                        value_text=EXACT_VALUE,
                        evidence_quote=EXACT_QUOTE,
                        confidence=ExtractionConfidence.HIGH,
                    )
                    for target_field in task.target_fields
                ]
            ),
            usage=AgentIterationUsage(
                agent="extractor",
                iteration=iteration,
                call_index=call_index,
                scope_task_ids=[task.task_id],
                scope_source_ids=[source.source_id],
                requested_model=self.model_name,
                resolved_model=self.model_name,
                tokens=TokenUsage(
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                ),
            ),
            source_id=source.source_id,
        )


class FixtureCheckerLLM:
    model_name = "fake-checker-model"

    def __init__(self, draft_factory):
        self.draft_factory = draft_factory
        self.calls = []

    def generate(
        self,
        plan,
        search_results,
        extraction_results,
        tasks,
        sources,
        system_prompt,
        *,
        iteration,
        call_index,
    ):
        self.calls.append(
            {
                "tasks": tasks,
                "sources": sources,
                "iteration": iteration,
                "call_index": call_index,
                "system_prompt": system_prompt,
            }
        )
        return CheckerGeneration(
            draft=self.draft_factory(extraction_results),
            usage=_checker_usage(
                iteration=iteration,
                task_ids=[task.task_id for task in tasks],
                source_ids=[source.source_id for source in sources],
            ),
        )


class FailingCheckerLLM:
    model_name = "fake-checker-model"

    def __init__(self, *, include_usage):
        self.include_usage = include_usage

    def generate(
        self,
        plan,
        search_results,
        extraction_results,
        tasks,
        sources,
        system_prompt,
        *,
        iteration,
        call_index,
    ):
        del plan, search_results, extraction_results, system_prompt
        usage = None
        if self.include_usage:
            usage = _checker_usage(
                iteration=iteration,
                task_ids=[task.task_id for task in tasks],
                source_ids=[source.source_id for source in sources],
            )
        raise CheckerProviderError(
            "Fixture provider failure.",
            code="incomplete_response",
            usage=usage,
            iteration=iteration,
            call_index=call_index,
            scope_task_ids=[task.task_id for task in tasks],
            scope_source_ids=[source.source_id for source in sources],
            requested_model=self.model_name,
        )


class CheckerAgentTests(TestCase):
    @classmethod
    def setUpClass(cls):
        complete_plan = PlannerAgent(load_question_catalog()).create_plan(
            PlannerInput(
                brand_name="Example",
                target_country="PL",
                depth="catalog",
            )
        )
        cls.second_task = complete_plan.tasks[1]
        plan_payload = complete_plan.model_dump(mode="python")
        plan_payload["tasks"] = [complete_plan.tasks[0]]
        plan_payload["critical_fields"] = list(
            complete_plan.tasks[0].target_fields
        )
        plan_payload["stop_conditions"]["quality_threshold"] = 80
        cls.plan = ResearchPlan.model_validate(plan_payload)
        cls.task = cls.plan.tasks[0]
        cls.sources = [
            cls._make_source(1, SourceType.OFFICIAL),
            cls._make_source(2, SourceType.REGISTRY),
            cls._make_source(3, SourceType.ROUTING_LEAD),
        ]
        cls.search_results = cls._make_search_results()
        cls.fetcher = FixtureFetcher()
        cls.extraction_results = ExtractorAgent(
            cls.fetcher,
            FixtureExtractorLLM(),
        ).create_extraction_results(
            cls.plan,
            cls.search_results,
            plan_sha256=PLAN_SHA256,
            search_sha256=SEARCH_SHA256,
            search_reference=SEARCH_REFERENCE,
            plan_reference=PLAN_REFERENCE,
            source_limit=len(cls.sources),
            max_api_calls=len(cls.sources),
        )
        cls.claims_for_first_field = [
            claim
            for claim in cls.extraction_results.claims
            if claim.target_field == cls.task.target_fields[0]
        ]

    @classmethod
    def _make_source(cls, number, source_type):
        source_id = f"source-{number:016x}"
        url = f"https://source{number}.example{number}.com/franchise"
        return SearchSource(
            source_id=source_id,
            url=url,
            canonical_url=url,
            title=f"Fixture source {number}",
            source_type=source_type,
            origin=SearchSourceOrigin.OPENAI_WEB_SEARCH,
            provider_observed=True,
            task_ids=[cls.task.task_id],
            observed_in_action_ids=["action-checker-fixture"],
            discovered_via_queries=[],
            relevance_note="Fixture evidence for the selected identity task.",
            discovered_at=NOW,
        )

    @classmethod
    def _make_search_results(cls):
        query = cls.task.search_queries[0]
        source_ids = [source.source_id for source in cls.sources]
        source_urls = [source.canonical_url for source in cls.sources]
        return SearchResults(
            search_id=str(uuid4()),
            plan_run_id=cls.plan.run_id,
            plan_sha256=PLAN_SHA256,
            plan_reference=PLAN_REFERENCE,
            created_at=NOW,
            iteration=1,
            generated_by="openai",
            model="fake-searcher-model",
            brand_name=cls.plan.planner_input.brand_name,
            target_country=cls.plan.planner_input.target_country,
            depth=cls.plan.planner_input.depth,
            search_executed=True,
            limits=SearchLimits(
                max_search_calls=1,
                task_limit=1,
                min_queries_per_task=1,
            ),
            selected_task_ids=[cls.task.task_id],
            unselected_task_ids=[],
            actions=[
                SearchAction(
                    action_id="action-checker-fixture",
                    call_index=1,
                    scope_task_ids=[cls.task.task_id],
                    action_type="search",
                    status="completed",
                    queries=[query],
                    source_urls=source_urls,
                )
            ],
            sources=cls.sources,
            task_results=[
                SearchTaskResult(
                    task_id=cls.task.task_id,
                    catalog_question_id=cls.task.catalog_question_id,
                    status=SearchTaskStatus.SOURCES_FOUND,
                    planned_queries=[query],
                    attempted_queries=[query],
                    planned_queries_attempted=[query],
                    derived_queries_attempted=[],
                    query_coverage=SearchQueryCoverage.COMPLETE,
                    minimum_query_attempts=1,
                    minimum_sources=cls.task.min_sources,
                    action_ids=["action-checker-fixture"],
                    source_ids=source_ids,
                    coverage_gaps=[],
                    unresolved_targets=[],
                )
            ],
            warnings=[],
            compliance_rules=cls.plan.compliance_rules,
            agent_usage=[
                AgentIterationUsage(
                    agent="searcher",
                    iteration=1,
                    call_index=1,
                    scope_task_ids=[cls.task.task_id],
                    requested_model="fake-searcher-model",
                    resolved_model="fake-searcher-model",
                    tokens=TokenUsage(
                        input_tokens=50,
                        output_tokens=10,
                        total_tokens=60,
                    ),
                    tool_usage=[build_web_search_tool_usage({"search": 1})],
                )
            ],
        )

    @staticmethod
    def _accepted_draft(extraction_results):
        return CheckerDraft(
            decisions=[
                CheckerClaimDecisionDraft(
                    claim_id=claim.claim_id,
                    verdict=CheckerModelVerdict.ACCEPTED,
                    semantic_fit=CheckerModelSemanticFit.DIRECT,
                    source_support=CheckerModelSourceSupport.SUFFICIENT,
                    rationale="The exact quote directly supports the raw claim.",
                )
                for claim in extraction_results.claims
            ]
        )

    def _run(
        self,
        llm=None,
        *,
        plan=None,
        search_results=None,
        extraction_results=None,
        **kwargs,
    ):
        return CheckerAgent(llm).create_check_results(
            plan or self.plan,
            search_results or self.search_results,
            extraction_results or self.extraction_results,
            plan_sha256=kwargs.pop("plan_sha256", PLAN_SHA256),
            search_sha256=kwargs.pop("search_sha256", SEARCH_SHA256),
            extraction_sha256=kwargs.pop(
                "extraction_sha256", EXTRACTION_SHA256
            ),
            extraction_reference=kwargs.pop(
                "extraction_reference", EXTRACTION_REFERENCE
            ),
            iteration=kwargs.pop("iteration", 3),
            **kwargs,
        )

    @classmethod
    def _profile_fixture(cls, profile_id, question_id):
        plan = PlannerAgent(
            load_question_catalog(),
            profile_catalog=load_profile_catalog(),
        ).create_plan(
            PlannerInput(
                brand_name="Example",
                target_country="PL",
                profile_id=profile_id,
            )
        )
        task = next(
            item for item in plan.tasks if item.catalog_question_id == question_id
        )
        sources = []
        for number in range(1, task.min_sources + 1):
            source_id = f"source-{(100 + number):016x}"
            url = f"https://profile{number}.example{number}.com/franchise"
            sources.append(
                SearchSource(
                    source_id=source_id,
                    url=url,
                    canonical_url=url,
                    title=f"Profile fixture source {number}",
                    source_type=task.preferred_source_types[0],
                    origin=SearchSourceOrigin.OPENAI_WEB_SEARCH,
                    provider_observed=True,
                    task_ids=[task.task_id],
                    observed_in_action_ids=["action-profile-checker"],
                    discovered_via_queries=[],
                    relevance_note="Profile fixture evidence for one selected task.",
                    discovered_at=NOW,
                )
            )
        query = task.search_queries[0]
        search_results = SearchResults(
            search_id=str(uuid4()),
            plan_run_id=plan.run_id,
            plan_sha256=PLAN_SHA256,
            plan_reference=PLAN_REFERENCE,
            created_at=NOW,
            iteration=1,
            generated_by="openai",
            model="fake-searcher-model",
            brand_name=plan.planner_input.brand_name,
            target_country=plan.planner_input.target_country,
            depth=plan.planner_input.depth,
            search_executed=True,
            limits=SearchLimits(
                max_search_calls=1,
                task_limit=1,
                min_queries_per_task=1,
            ),
            selected_task_ids=[task.task_id],
            unselected_task_ids=[
                item.task_id for item in plan.tasks if item.task_id != task.task_id
            ],
            actions=[
                SearchAction(
                    action_id="action-profile-checker",
                    call_index=1,
                    scope_task_ids=[task.task_id],
                    action_type="search",
                    status="completed",
                    queries=[query],
                    source_urls=[source.canonical_url for source in sources],
                )
            ],
            sources=sources,
            task_results=[
                SearchTaskResult(
                    task_id=task.task_id,
                    catalog_question_id=task.catalog_question_id,
                    status=SearchTaskStatus.SOURCES_FOUND,
                    planned_queries=[query],
                    attempted_queries=[query],
                    planned_queries_attempted=[query],
                    derived_queries_attempted=[],
                    query_coverage=SearchQueryCoverage.COMPLETE,
                    minimum_query_attempts=1,
                    minimum_sources=task.min_sources,
                    action_ids=["action-profile-checker"],
                    source_ids=[source.source_id for source in sources],
                    coverage_gaps=[],
                    unresolved_targets=[],
                )
            ],
            warnings=[],
            compliance_rules=plan.compliance_rules,
            agent_usage=[
                AgentIterationUsage(
                    agent="searcher",
                    iteration=1,
                    call_index=1,
                    scope_task_ids=[task.task_id],
                    requested_model="fake-searcher-model",
                    resolved_model="fake-searcher-model",
                    tokens=TokenUsage(
                        input_tokens=50,
                        output_tokens=10,
                        total_tokens=60,
                    ),
                    tool_usage=[build_web_search_tool_usage({"search": 1})],
                )
            ],
        )
        extraction_results = ExtractorAgent(
            FixtureFetcher(),
            FixtureExtractorLLM(),
        ).create_extraction_results(
            plan,
            search_results,
            plan_sha256=PLAN_SHA256,
            search_sha256=SEARCH_SHA256,
            search_reference=SEARCH_REFERENCE,
            plan_reference=PLAN_REFERENCE,
            source_limit=len(sources),
            max_api_calls=len(sources),
        )
        return plan, task, search_results, extraction_results

    def test_free_leaves_every_claim_not_reviewed_and_applies_source_policy(self):
        results = self._run()

        self.assertEqual(results.generated_by, "deterministic")
        self.assertFalse(results.provider_executed)
        self.assertEqual(results.agent_usage, [])
        self.assertEqual(results.failed_attempts, [])
        self.assertEqual(results.selected_claim_ids, [
            claim.claim_id for claim in self.extraction_results.claims
        ])
        self.assertTrue(results.claim_decisions)
        self.assertTrue(
            all(
                decision.verdict == CheckerVerdict.NOT_REVIEWED
                for decision in results.claim_decisions
            )
        )
        self.assertTrue(
            all(
                field.status == CheckerFieldStatus.NOT_REVIEWED
                for task in results.task_results
                for field in task.field_results
            )
        )
        self.assertFalse(results.passed)
        self.assertEqual(
            results.recommended_next_action,
            CheckerNextAction.RUN_PAID_CHECKER,
        )
        assessments = {
            item.source_id: item for item in results.source_assessments
        }
        official = assessments[self.sources[0].source_id]
        self.assertEqual(
            official.authority_class,
            SourceAuthorityClass.PRIMARY_SELF_REPORT,
        )
        self.assertEqual(official.independence, SourceIndependence.FIRST_PARTY)
        self.assertEqual(official.reliability_score, 80)
        registry = assessments[self.sources[1].source_id]
        self.assertEqual(
            registry.authority_class,
            SourceAuthorityClass.PRIMARY_AUTHORITY,
        )
        self.assertEqual(registry.independence, SourceIndependence.INDEPENDENT)
        self.assertEqual(registry.reliability_score, 95)
        routing = assessments[self.sources[2].source_id]
        self.assertEqual(routing.authority_class, SourceAuthorityClass.ROUTING_ONLY)
        self.assertEqual(routing.reliability_score, 5)

    def test_v1_source_quality_metric_loads_under_v11_name(self):
        score = CheckerScoreBreakdown.model_validate(
            {
                "scoring_version": "checker-scoring-v1",
                "raw_coverage_score": 17,
                "verified_coverage_score": 3,
                "semantic_acceptance_score": 38,
                "source_quality_score": 80,
                "whole_plan_coverage_score": 0,
                "deduction_points": 0,
                "quality_score": 3,
            }
        )

        self.assertEqual(score.accepted_claim_source_quality_score, 80)
        self.assertNotIn("source_quality_score", score.model_dump())

    def test_exact_paid_decisions_are_locally_scored_and_can_pass(self):
        llm = FixtureCheckerLLM(self._accepted_draft)

        results = self._run(llm)

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(llm.calls[0]["call_index"], 1)
        self.assertEqual(results.generated_by, "openai")
        self.assertTrue(results.provider_executed)
        self.assertEqual(len(results.agent_usage), 1)
        self.assertEqual(results.failed_attempts, [])
        self.assertTrue(
            all(
                field.status == CheckerFieldStatus.VERIFIED
                for task in results.task_results
                for field in task.field_results
            )
        )
        self.assertEqual(results.critical_missing_fields, [])
        self.assertEqual(results.score_breakdown.raw_coverage_score, 100)
        self.assertEqual(results.score_breakdown.verified_coverage_score, 95)
        self.assertEqual(results.score_breakdown.semantic_acceptance_score, 100)
        self.assertEqual(
            results.score_breakdown.accepted_claim_source_quality_score,
            88,
        )
        self.assertEqual(results.quality_score, 95)
        self.assertTrue(results.passed)
        self.assertEqual(
            results.recommended_next_action,
            CheckerNextAction.HUMAN_REVIEW,
        )

    def test_profile_optional_gap_does_not_block_completion_gate(self):
        plan, task, search_results, extraction_results = self._profile_fixture(
            "PL:L1", "scope.brand_identity"
        )
        policy = next(
            question
            for question in plan.profile_snapshot.questions
            if question.question_id == task.catalog_question_id
        )
        optional_field = next(
            field.target_field
            for field in policy.fields
            if field.availability == FieldAvailability.PUBLIC_OPTIONAL
        )

        def draft_factory(results):
            return CheckerDraft(
                decisions=[
                    CheckerClaimDecisionDraft(
                        claim_id=claim.claim_id,
                        verdict=(
                            CheckerModelVerdict.REJECTED
                            if claim.target_field == optional_field
                            else CheckerModelVerdict.ACCEPTED
                        ),
                        semantic_fit=(
                            CheckerModelSemanticFit.MISMATCH
                            if claim.target_field == optional_field
                            else CheckerModelSemanticFit.DIRECT
                        ),
                        source_support=CheckerModelSourceSupport.SUFFICIENT,
                        issue_codes=(
                            [CheckerIssueCode.UNSUPPORTED_CLAIM]
                            if claim.target_field == optional_field
                            else []
                        ),
                        rationale="Fixture profile field decision is deterministic.",
                    )
                    for claim in results.claims
                ]
            )

        results = self._run(
            FixtureCheckerLLM(draft_factory),
            plan=plan,
            search_results=search_results,
            extraction_results=extraction_results,
        )

        optional_result = next(
            field
            for result in results.task_results
            for field in result.field_results
            if field.target_field == optional_field
        )
        follow_up = next(
            item
            for item in results.follow_up_tasks
            if item.target_field == optional_field
        )
        self.assertEqual(results.schema_version, "1.5.0")
        self.assertEqual(results.profile_id, "PL:L1:v1")
        self.assertFalse(optional_result.required_for_completion)
        self.assertEqual(
            optional_result.availability, FieldAvailability.PUBLIC_OPTIONAL
        )
        self.assertNotIn(optional_field, results.critical_missing_fields)
        self.assertTrue(results.selected_scope_ready)
        self.assertFalse(results.passed)
        self.assertEqual(
            results.recommended_next_action,
            CheckerNextAction.RESEARCH_NEXT_BATCH,
        )
        self.assertEqual(follow_up.route, CheckerFollowUpRoute.RESOLVER)
        self.assertFalse(follow_up.required_for_completion)
        self.assertGreater(
            results.score_breakdown.completion_coverage_score,
            results.score_breakdown.total_coverage_score,
        )
        self.assertGreater(
            results.score_breakdown.completion_coverage_score,
            results.score_breakdown.whole_plan_completion_coverage_score,
        )

    def test_profile_required_field_in_high_priority_task_is_critical(self):
        plan, task, search_results, extraction_results = self._profile_fixture(
            "PL:L1", "scope.jurisdictions"
        )
        policy = next(
            question
            for question in plan.profile_snapshot.questions
            if question.question_id == task.catalog_question_id
        )
        required_field = next(
            field.target_field
            for field in policy.fields
            if field.required_for_completion
        )

        def draft_factory(results):
            return CheckerDraft(
                decisions=[
                    CheckerClaimDecisionDraft(
                        claim_id=claim.claim_id,
                        verdict=(
                            CheckerModelVerdict.REJECTED
                            if claim.target_field == required_field
                            else CheckerModelVerdict.ACCEPTED
                        ),
                        semantic_fit=(
                            CheckerModelSemanticFit.MISMATCH
                            if claim.target_field == required_field
                            else CheckerModelSemanticFit.DIRECT
                        ),
                        source_support=CheckerModelSourceSupport.SUFFICIENT,
                        issue_codes=(
                            [CheckerIssueCode.UNSUPPORTED_CLAIM]
                            if claim.target_field == required_field
                            else []
                        ),
                        rationale="Fixture profile field decision is deterministic.",
                    )
                    for claim in results.claims
                ]
            )

        results = self._run(
            FixtureCheckerLLM(draft_factory),
            plan=plan,
            search_results=search_results,
            extraction_results=extraction_results,
        )

        self.assertNotEqual(task.priority.value, "critical")
        self.assertIn(required_field, results.critical_missing_fields)
        self.assertFalse(results.selected_scope_ready)

    def test_profile_private_and_system_gaps_use_bounded_routes(self):
        cases = (
            (
                "fdd06.other_fees",
                FieldAvailability.PRIVATE_DOCUMENT_REQUIRED,
                CheckerFollowUpRoute.HUMAN_REVIEW,
                CheckerFollowUpAction.REQUEST_AUTHORIZED_DOCUMENT,
            ),
            (
                "scope.document_inventory",
                FieldAvailability.SYSTEM_DERIVED,
                CheckerFollowUpRoute.RESOLVER,
                CheckerFollowUpAction.LOCAL_AUDIT,
            ),
        )
        for question_id, availability, route, action in cases:
            with self.subTest(question=question_id):
                plan, task, search_results, extraction_results = self._profile_fixture(
                    "PL:L3", question_id
                )
                policy = next(
                    question
                    for question in plan.profile_snapshot.questions
                    if question.question_id == question_id
                )
                target_field = next(
                    field.target_field
                    for field in policy.fields
                    if field.availability == availability
                )

                def draft_factory(results):
                    return CheckerDraft(
                        decisions=[
                            CheckerClaimDecisionDraft(
                                claim_id=claim.claim_id,
                                verdict=(
                                    CheckerModelVerdict.REJECTED
                                    if claim.target_field == target_field
                                    else CheckerModelVerdict.ACCEPTED
                                ),
                                semantic_fit=(
                                    CheckerModelSemanticFit.MISMATCH
                                    if claim.target_field == target_field
                                    else CheckerModelSemanticFit.DIRECT
                                ),
                                source_support=CheckerModelSourceSupport.SUFFICIENT,
                                rationale="Fixture routes a profile-specific gap.",
                            )
                            for claim in results.claims
                        ]
                    )

                results = self._run(
                    FixtureCheckerLLM(draft_factory),
                    plan=plan,
                    search_results=search_results,
                    extraction_results=extraction_results,
                )
                follow_up = next(
                    item
                    for item in results.follow_up_tasks
                    if item.target_field == target_field
                )
                self.assertEqual(follow_up.availability, availability)
                self.assertEqual(follow_up.route, route)
                self.assertEqual(follow_up.action, action)
                self.assertEqual(follow_up.candidate_source_ids, [])
                self.assertEqual(follow_up.retry_source_ids, [])
                self.assertEqual(follow_up.reextract_source_ids, [])

    def test_not_applicable_profile_field_has_no_follow_up(self):
        paid_results = self._run(FixtureCheckerLLM(self._accepted_draft))
        policy_by_field = {
            field: (
                FieldAvailability.PUBLIC_EXPECTED,
                True,
                ProfileReuseScope.BRAND,
            )
            for field in self.task.target_fields
        }
        target_field = self.task.target_fields[0]
        policy_by_field[target_field] = (
            FieldAvailability.NOT_APPLICABLE,
            False,
            ProfileReuseScope.BRAND,
        )

        task_results, follow_ups = CheckerAgent._build_task_results(
            self.plan,
            [self.task],
            self.search_results,
            self.extraction_results,
            paid_results.claim_decisions,
            [],
            {source.source_id: source for source in self.sources},
            {
                assessment.source_id: assessment
                for assessment in paid_results.source_assessments
            },
            policy_by_field,
            paid_success=True,
        )

        field_result = next(
            field
            for field in task_results[0].field_results
            if field.target_field == target_field
        )
        self.assertEqual(field_result.status, CheckerFieldStatus.NOT_APPLICABLE)
        self.assertFalse(
            any(item.target_field == target_field for item in follow_ups)
        )

    def test_checker_14_artifact_remains_readable(self):
        current = self._run(FixtureCheckerLLM(self._accepted_draft))
        payload = current.model_dump(mode="json")
        payload["schema_version"] = "1.4.0"
        payload.pop("profile_id")
        payload.pop("profile_sha256")
        payload["score_breakdown"]["scoring_version"] = "checker-scoring-v2"
        for field_name in (
            "completion_coverage_score",
            "total_coverage_score",
            "whole_plan_completion_coverage_score",
            "whole_plan_total_coverage_score",
        ):
            payload["score_breakdown"].pop(field_name)
        for task_result in payload["task_results"]:
            for field_result in task_result["field_results"]:
                field_result.pop("availability")
                field_result.pop("required_for_completion")
                field_result.pop("reuse_scope")
        for follow_up in payload["follow_up_tasks"]:
            follow_up.pop("availability")
            follow_up.pop("required_for_completion")
            follow_up.pop("reuse_scope")

        restored = CheckerResults.model_validate(payload)

        self.assertEqual(restored.schema_version, "1.4.0")
        self.assertEqual(
            restored.score_breakdown.scoring_version, "checker-scoring-v2"
        )

    def test_incremental_checker_inherits_unchanged_paid_scope_without_api_call(self):
        prior = self._run(FixtureCheckerLLM(self._accepted_draft))
        current = self.extraction_results.model_copy(
            update={
                "generated_by": "executor",
                "resolution_id": str(uuid4()),
                "resolution_sha256": "d" * 64,
                "resolution_reference": "/fixtures/resolution.json",
                "prior_extraction_id": self.extraction_results.extraction_id,
                "prior_extraction_sha256": EXTRACTION_SHA256,
                "prior_extraction_reference": EXTRACTION_REFERENCE,
            }
        )
        llm = FixtureCheckerLLM(self._accepted_draft)

        results = self._run(
            llm,
            extraction_results=current,
            extraction_sha256="e" * 64,
            iteration=4,
            prior_checker_results=prior,
            prior_checker_sha256="f" * 64,
            prior_checker_reference="/fixtures/check-r003.json",
            prior_extraction_results=self.extraction_results,
            prior_extraction_sha256=EXTRACTION_SHA256,
            prior_search_results=self.search_results,
            prior_search_sha256=SEARCH_SHA256,
        )

        self.assertEqual(results.checker_mode, CheckerMode.INCREMENTAL)
        self.assertEqual(results.reviewed_task_ids, [])
        self.assertEqual(results.reviewed_claim_ids, [])
        self.assertEqual(results.inherited_claim_ids, results.selected_claim_ids)
        self.assertEqual(results.agent_usage, [])
        self.assertEqual(llm.calls, [])
        self.assertTrue(results.passed)

    def test_incremental_checker_reviews_entire_task_when_one_claim_changes(self):
        prior = self._run(FixtureCheckerLLM(self._accepted_draft))
        changed_claim = self.extraction_results.claims[0].model_copy(
            update={"value_text": "Changed value forces task-level review."}
        )
        current = self.extraction_results.model_copy(
            update={
                "generated_by": "executor",
                "resolution_id": str(uuid4()),
                "resolution_sha256": "d" * 64,
                "resolution_reference": "/fixtures/resolution.json",
                "prior_extraction_id": self.extraction_results.extraction_id,
                "prior_extraction_sha256": EXTRACTION_SHA256,
                "prior_extraction_reference": EXTRACTION_REFERENCE,
                "claims": [
                    changed_claim,
                    *self.extraction_results.claims[1:],
                ],
            }
        )
        llm = FixtureCheckerLLM(self._accepted_draft)

        results = self._run(
            llm,
            extraction_results=current,
            extraction_sha256="e" * 64,
            iteration=4,
            prior_checker_results=prior,
            prior_checker_sha256="f" * 64,
            prior_checker_reference="/fixtures/check-r003.json",
            prior_extraction_results=self.extraction_results,
            prior_extraction_sha256=EXTRACTION_SHA256,
            prior_search_results=self.search_results,
            prior_search_sha256=SEARCH_SHA256,
        )

        self.assertEqual(results.reviewed_task_ids, [self.task.task_id])
        self.assertEqual(results.reviewed_claim_ids, results.selected_claim_ids)
        self.assertEqual(results.inherited_claim_ids, [])
        self.assertEqual(len(llm.calls), 1)

    def test_direct_claim_is_accepted_even_when_it_needs_corroboration(self):
        target_field = self.task.target_fields[0]

        def draft_factory(extraction_results):
            decisions = []
            for claim in extraction_results.claims:
                needs_corroboration = claim.target_field == target_field
                decisions.append(
                    CheckerClaimDecisionDraft(
                        claim_id=claim.claim_id,
                        verdict=(
                            CheckerModelVerdict.NEEDS_REVIEW
                            if needs_corroboration
                            else CheckerModelVerdict.ACCEPTED
                        ),
                        semantic_fit=CheckerModelSemanticFit.DIRECT,
                        source_support=(
                            CheckerModelSourceSupport.NEEDS_CORROBORATION
                            if needs_corroboration
                            else CheckerModelSourceSupport.SUFFICIENT
                        ),
                        issue_codes=(
                            [CheckerIssueCode.NEEDS_INDEPENDENT_CORROBORATION]
                            if needs_corroboration
                            else []
                        ),
                        rationale="The quote is direct but corroboration remains.",
                    )
                )
            return CheckerDraft(decisions=decisions)

        results = self._run(FixtureCheckerLLM(draft_factory))
        field = results.task_results[0].field_results[0]

        self.assertTrue(
            all(
                decision.verdict == CheckerVerdict.ACCEPTED
                for decision in results.claim_decisions
                if decision.target_field == target_field
            )
        )
        self.assertEqual(field.status, CheckerFieldStatus.NEEDS_CORROBORATION)
        self.assertEqual(field.needs_review_claim_ids, [])

    def test_rejected_claim_cannot_create_scored_contradiction(self):
        target_claims = [
            claim
            for claim in self.extraction_results.claims
            if claim.target_field == self.task.target_fields[0]
        ]
        rejected_claim_id = target_claims[1].claim_id

        def draft_factory(extraction_results):
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
                        source_support=(
                            CheckerModelSourceSupport.UNSUITABLE
                            if claim.claim_id == rejected_claim_id
                            else CheckerModelSourceSupport.SUFFICIENT
                        ),
                        issue_codes=(
                            [CheckerIssueCode.UNSUPPORTED_CLAIM]
                            if claim.claim_id == rejected_claim_id
                            else []
                        ),
                        rationale="The fixture supplies a deterministic verdict.",
                    )
                    for claim in extraction_results.claims
                ],
                contradictions=[
                    CheckerContradictionDraft(
                        target_field=self.task.target_fields[0],
                        claim_ids=[claim.claim_id for claim in target_claims],
                        kind=CheckerContradictionKind.TEMPORAL_MISMATCH,
                        rationale=(
                            "The provider incorrectly compares accepted and "
                            "rejected claims."
                        ),
                    )
                ],
            )

        results = self._run(FixtureCheckerLLM(draft_factory))

        self.assertEqual(results.contradictions, [])
        self.assertEqual(results.score_breakdown.deduction_points, 0)
        field = results.task_results[0].field_results[0]
        self.assertNotEqual(field.status, CheckerFieldStatus.CONFLICTING)

    def test_equivalent_polish_currency_aliases_are_not_a_contradiction(self):
        claims = [
            RawExtractionClaim(
                claim_id=f"claim-{index:016x}",
                task_id="task-investment",
                target_field="investment.currency",
                value_text=value,
                currency_text=value,
                citation_ids=[f"citation-{index:016x}"],
                confidence=ExtractionConfidence.HIGH,
            )
            for index, value in ((1, "zł"), (2, "złotych"))
        ]
        draft = CheckerDraft(
            decisions=[
                CheckerClaimDecisionDraft(
                    claim_id=claim.claim_id,
                    verdict=CheckerModelVerdict.ACCEPTED,
                    semantic_fit=CheckerModelSemanticFit.DIRECT,
                    source_support=CheckerModelSourceSupport.SUFFICIENT,
                    rationale="The quote directly identifies the currency.",
                )
                for claim in claims
            ],
            contradictions=[
                CheckerContradictionDraft(
                    target_field="investment.currency",
                    claim_ids=[claim.claim_id for claim in claims],
                    kind=CheckerContradictionKind.SCOPE_MISMATCH,
                    rationale="The surrounding amounts differ.",
                )
            ],
        )

        _, contradictions, _ = CheckerAgent._ground_draft(
            draft,
            claims,
            {
                f"citation-{index:016x}": f"source-{index:016x}"
                for index in (1, 2)
            },
            [f"source-{index:016x}" for index in (1, 2)],
        )

        self.assertEqual(contradictions, [])

    def test_terminal_anti_bot_document_is_not_retryable(self):
        document = self.extraction_results.documents[0].model_copy(
            update={
                "retrieval_status": DocumentRetrievalStatus.NOT_ACCESSIBLE,
                "error_code": "anti_bot_page",
            }
        )
        transient = document.model_copy(
            update={
                "retrieval_status": DocumentRetrievalStatus.FAILED,
                "error_code": "tls_error",
            }
        )

        self.assertFalse(_document_is_retryable(document))
        self.assertTrue(_document_is_retryable(transient))

    def test_mixed_multivalue_field_is_partial_and_routes_unresolved_claim(self):
        target_field = self.task.target_fields[0]
        first_target_claim = next(
            claim
            for claim in self.extraction_results.claims
            if claim.target_field == target_field
        )

        def draft_factory(extraction_results):
            return CheckerDraft(
                decisions=[
                    CheckerClaimDecisionDraft(
                        claim_id=claim.claim_id,
                        verdict=(
                            CheckerModelVerdict.REJECTED
                            if claim.claim_id == first_target_claim.claim_id
                            else CheckerModelVerdict.ACCEPTED
                        ),
                        semantic_fit=(
                            CheckerModelSemanticFit.MISMATCH
                            if claim.claim_id == first_target_claim.claim_id
                            else CheckerModelSemanticFit.DIRECT
                        ),
                        source_support=CheckerModelSourceSupport.SUFFICIENT,
                        issue_codes=(
                            [CheckerIssueCode.UNSUPPORTED_CLAIM]
                            if claim.claim_id == first_target_claim.claim_id
                            else []
                        ),
                        rationale="The fixture creates a mixed-value field result.",
                    )
                    for claim in extraction_results.claims
                ]
            )

        results = self._run(FixtureCheckerLLM(draft_factory))
        field = results.task_results[0].field_results[0]
        follow_up = next(
            item
            for item in results.follow_up_tasks
            if item.target_field == target_field
        )

        self.assertEqual(field.status, CheckerFieldStatus.PARTIAL)
        self.assertGreater(field.quality_points, 0)
        self.assertEqual(
            follow_up.reason,
            CheckerFollowUpReason.COMPLETE_PARTIAL_FIELD,
        )
        self.assertEqual(
            follow_up.related_claim_ids,
            [first_target_claim.claim_id],
        )
        self.assertTrue(follow_up.supporting_claim_ids)
        self.assertEqual(
            follow_up.action,
            CheckerFollowUpAction.REEXTRACT_EXISTING,
        )
        self.assertTrue(follow_up.reextract_source_ids)

    def test_follow_up_preserves_more_than_twenty_related_claims(self):
        target_field = self.task.target_fields[0]
        template = next(
            claim
            for claim in self.extraction_results.claims
            if claim.target_field == target_field
        )
        other_claims = [
            claim
            for claim in self.extraction_results.claims
            if claim.target_field != target_field
        ]
        target_claims = [
            template.model_copy(
                update={"claim_id": f"claim-{index:016x}"}
            )
            for index in range(100, 122)
        ]
        extraction_results = self.extraction_results.model_copy(
            update={"claims": [*target_claims, *other_claims]}
        )

        def draft_factory(results):
            return CheckerDraft(
                decisions=[
                    CheckerClaimDecisionDraft(
                        claim_id=claim.claim_id,
                        verdict=(
                            CheckerModelVerdict.REJECTED
                            if claim.target_field == target_field
                            else CheckerModelVerdict.ACCEPTED
                        ),
                        semantic_fit=(
                            CheckerModelSemanticFit.MISMATCH
                            if claim.target_field == target_field
                            else CheckerModelSemanticFit.DIRECT
                        ),
                        source_support=CheckerModelSourceSupport.SUFFICIENT,
                        issue_codes=(
                            [CheckerIssueCode.UNSUPPORTED_CLAIM]
                            if claim.target_field == target_field
                            else []
                        ),
                        rationale="The fixture exercises accumulated field lineage.",
                    )
                    for claim in results.claims
                ]
            )

        results = self._run(
            FixtureCheckerLLM(draft_factory),
            extraction_results=extraction_results,
        )
        follow_up = next(
            item
            for item in results.follow_up_tasks
            if item.target_field == target_field
        )

        self.assertEqual(follow_up.related_claim_ids, [
            claim.claim_id for claim in target_claims
        ])

    def test_paid_postprocessing_failure_retains_provider_usage(self):
        with patch.object(
            CheckerAgent,
            "_build_task_results",
            side_effect=RuntimeError("fixture assembly failure"),
        ):
            with self.assertRaises(CheckerProviderError) as raised:
                self._run(FixtureCheckerLLM(self._accepted_draft))

        self.assertEqual(raised.exception.code, "postprocessing_error")
        self.assertEqual(len(raised.exception.usages), 1)
        self.assertEqual(raised.exception.usages[0].agent, "checker")

    def test_critical_missing_field_blocks_pass_above_threshold(self):
        blocked_field = self.task.target_fields[0]

        def draft_factory(extraction_results):
            return CheckerDraft(
                decisions=[
                    CheckerClaimDecisionDraft(
                        claim_id=claim.claim_id,
                        verdict=(
                            CheckerModelVerdict.REJECTED
                            if claim.target_field == blocked_field
                            else CheckerModelVerdict.ACCEPTED
                        ),
                        semantic_fit=(
                            CheckerModelSemanticFit.MISMATCH
                            if claim.target_field == blocked_field
                            else CheckerModelSemanticFit.DIRECT
                        ),
                        source_support=CheckerModelSourceSupport.SUFFICIENT,
                        issue_codes=(
                            [CheckerIssueCode.UNSUPPORTED_CLAIM]
                            if claim.target_field == blocked_field
                            else []
                        ),
                        rationale="The fixture returns a deliberate field decision.",
                    )
                    for claim in extraction_results.claims
                ]
            )

        results = self._run(FixtureCheckerLLM(draft_factory))

        self.assertGreaterEqual(results.quality_score, results.quality_threshold)
        self.assertIn(blocked_field, results.critical_missing_fields)
        self.assertFalse(results.passed)
        self.assertEqual(
            results.recommended_next_action,
            CheckerNextAction.RESOLVE_GAPS,
        )

    def test_high_severity_unsafe_item_blocks_otherwise_passing_result(self):
        def draft_factory(extraction_results):
            draft = self._accepted_draft(extraction_results)
            return draft.model_copy(
                update={
                    "unsafe_items": [
                        CheckerUnsafeItemDraft(
                            category=CheckerUnsafeCategory.SENSITIVE_UNCORROBORATED,
                            severity=CheckerSeverity.HIGH,
                            claim_ids=[extraction_results.claims[0].claim_id],
                            rationale="The fixture marks a blocking sensitive claim.",
                        )
                    ]
                }
            )

        results = self._run(FixtureCheckerLLM(draft_factory))

        self.assertGreaterEqual(results.quality_score, results.quality_threshold)
        self.assertEqual(results.score_breakdown.deduction_points, 10)
        self.assertEqual(len(results.unsafe_items), 1)
        self.assertFalse(results.passed)
        self.assertEqual(
            results.recommended_next_action,
            CheckerNextAction.RESOLVE_GAPS,
        )

    def test_partial_plan_scope_blocks_pass_despite_high_selected_score(self):
        plan_payload = self.plan.model_dump(mode="python")
        plan_payload["tasks"] = [self.task, self.second_task]
        expanded_plan = ResearchPlan.model_validate(plan_payload)
        expanded_search = self.search_results.model_copy(
            update={"unselected_task_ids": [self.second_task.task_id]}
        )

        results = self._run(
            FixtureCheckerLLM(self._accepted_draft),
            plan=expanded_plan,
            search_results=expanded_search,
        )

        self.assertGreaterEqual(results.quality_score, results.quality_threshold)
        self.assertFalse(results.scope_complete)
        self.assertTrue(results.selected_scope_ready)
        self.assertEqual(results.unevaluated_task_ids, [self.second_task.task_id])
        self.assertFalse(results.passed)
        self.assertEqual(
            results.recommended_next_action,
            CheckerNextAction.RESEARCH_NEXT_BATCH,
        )

    def test_local_unit_format_contract_rejects_store_equipment_mapping(self):
        claim = RawExtractionClaim(
            claim_id="claim-aaaaaaaaaaaaaaaa",
            task_id="task-offer-format",
            target_field="offer.unit_formats",
            value_text="umeblowany i w pełni wyposażony sklep",
            citation_ids=["citation-aaaaaaaaaaaaaaaa"],
            confidence=ExtractionConfidence.HIGH,
        )
        draft = CheckerDraft(
            decisions=[
                CheckerClaimDecisionDraft(
                    claim_id=claim.claim_id,
                    verdict=CheckerModelVerdict.ACCEPTED,
                    semantic_fit=CheckerModelSemanticFit.DIRECT,
                    source_support=CheckerModelSourceSupport.SUFFICIENT,
                    rationale="The fixture deliberately accepts a bad field mapping.",
                )
            ]
        )

        decisions, _, _ = CheckerAgent._ground_draft(
            draft,
            [claim],
            {"citation-aaaaaaaaaaaaaaaa": "source-aaaaaaaaaaaaaaaa"},
            ["source-aaaaaaaaaaaaaaaa"],
        )

        self.assertEqual(decisions[0].verdict, CheckerVerdict.REJECTED)
        self.assertEqual(decisions[0].semantic_fit, CheckerSemanticFit.MISMATCH)
        self.assertIn(
            CheckerIssueCode.UNSUPPORTED_FIELD_MAPPING,
            decisions[0].issue_codes,
        )

    def test_local_unit_format_contract_allows_transaction_structure(self):
        claim = RawExtractionClaim(
            claim_id="claim-bbbbbbbbbbbbbbbb",
            task_id="task-offer-format",
            target_field="offer.unit_formats",
            value_text="model single-unit obejmujący jeden sklep",
            citation_ids=["citation-bbbbbbbbbbbbbbbb"],
            confidence=ExtractionConfidence.HIGH,
        )
        draft = CheckerDraft(
            decisions=[
                CheckerClaimDecisionDraft(
                    claim_id=claim.claim_id,
                    verdict=CheckerModelVerdict.ACCEPTED,
                    semantic_fit=CheckerModelSemanticFit.DIRECT,
                    source_support=CheckerModelSourceSupport.SUFFICIENT,
                    rationale="The quote identifies a supported transaction structure.",
                )
            ]
        )

        decisions, _, _ = CheckerAgent._ground_draft(
            draft,
            [claim],
            {"citation-bbbbbbbbbbbbbbbb": "source-bbbbbbbbbbbbbbbb"},
            ["source-bbbbbbbbbbbbbbbb"],
        )

        self.assertEqual(decisions[0].verdict, CheckerVerdict.ACCEPTED)

    def test_invalid_provider_claim_coverage_retains_usage_as_failed_attempt(self):
        def invalid_draft(extraction_results):
            exact = self._accepted_draft(extraction_results)
            return exact.model_copy(update={"decisions": exact.decisions[:-1]})

        results = self._run(FixtureCheckerLLM(invalid_draft))

        self.assertEqual(len(results.agent_usage), 1)
        self.assertEqual(len(results.failed_attempts), 1)
        failure = results.failed_attempts[0]
        self.assertEqual(failure.error_code, "invalid_checker_output")
        self.assertTrue(failure.usage_recorded)
        self.assertFalse(failure.token_usage_unknown)
        self.assertTrue(
            all(
                decision.verdict == CheckerVerdict.NOT_REVIEWED
                for decision in results.claim_decisions
            )
        )
        self.assertFalse(results.passed)
        self.assertEqual(
            results.recommended_next_action,
            CheckerNextAction.RETRY_CHECKER,
        )

    def test_rejects_invalid_hashes_and_lineage_before_checking(self):
        cases = [
            (
                {"plan_sha256": "A" * 64},
                "Plan artifact SHA-256",
            ),
            (
                {"extraction_sha256": "short"},
                "Extractor artifact SHA-256",
            ),
            (
                {
                    "search_results": self.search_results.model_copy(
                        update={"plan_sha256": "d" * 64}
                    )
                },
                "Searcher plan SHA-256",
            ),
            (
                {
                    "extraction_results": self.extraction_results.model_copy(
                        update={"search_sha256": "d" * 64}
                    )
                },
                "Extractor Searcher SHA-256",
            ),
            (
                {"extraction_reference": " "},
                "reference cannot be blank",
            ),
        ]
        for arguments, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(CheckerValidationError, message):
                    self._run(**arguments)

    def test_rejects_iteration_claim_and_evidence_limits(self):
        self.assertGreater(len(self.extraction_results.claims), 1)
        evidence_chars = sum(
            len(citation.quote)
            for claim in self.extraction_results.claims
            for citation_id in claim.citation_ids
            for citation in self.extraction_results.citations
            if citation.citation_id == citation_id
        )
        self.assertGreater(evidence_chars, 1_000)
        cases = [
            ({"iteration": 0}, "iteration must be positive"),
            ({"max_claims": 0}, "max_claims must be between"),
            ({"max_claims": 501}, "max_claims must be between"),
            (
                {"max_claims": len(self.extraction_results.claims) - 1},
                "Extractor has .* claims",
            ),
            ({"max_evidence_chars": 999}, "max_evidence_chars must be between"),
            ({"max_evidence_chars": 1_000}, "evidence payload has"),
            ({"max_evidence_chars": 500_001}, "max_evidence_chars must be between"),
        ]
        for arguments, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(CheckerValidationError, message):
                    self._run(**arguments)

    def test_paid_provider_failure_ledgers_known_and_unknown_usage(self):
        for include_usage in (True, False):
            with self.subTest(include_usage=include_usage):
                results = self._run(
                    FailingCheckerLLM(include_usage=include_usage)
                )

                self.assertEqual(len(results.failed_attempts), 1)
                failure = results.failed_attempts[0]
                self.assertEqual(failure.error_code, "incomplete_response")
                self.assertEqual(failure.usage_recorded, include_usage)
                self.assertEqual(failure.token_usage_unknown, not include_usage)
                self.assertEqual(len(results.agent_usage), int(include_usage))
                self.assertEqual(
                    failure.scope_task_ids,
                    self.extraction_results.selected_task_ids,
                )
                self.assertEqual(
                    failure.scope_source_ids,
                    self.extraction_results.selected_source_ids,
                )
                self.assertTrue(results.provider_executed)
                self.assertTrue(
                    all(
                        decision.verdict == CheckerVerdict.NOT_REVIEWED
                        for decision in results.claim_decisions
                    )
                )
                self.assertFalse(results.passed)
                self.assertEqual(
                    results.recommended_next_action,
                    CheckerNextAction.RETRY_CHECKER,
                )
