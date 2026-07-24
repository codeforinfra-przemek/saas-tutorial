"""Resolver agent: turn Checker gaps into a bounded next-round repair plan."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..llm.protocol import ResolverLLM, ResolverProviderError
from ..schemas import (
    AgentIterationUsage,
    CheckerFollowUpAction,
    CheckerFollowUpReason,
    CheckerFollowUpRoute,
    CheckerFollowUpTask,
    CheckerIssueCode,
    CheckerNextAction,
    CheckerResults,
    DocumentParseStatus,
    DocumentRetrievalStatus,
    ExtractionResults,
    FieldAvailability,
    PRIORITY_ORDER,
    ProfileReuseScope,
    ResearchPlan,
    ResolverAction,
    ResolverAttemptFailure,
    ResolverDraft,
    ResolverExecutionBatch,
    ResolverLimits,
    ResolverNextAction,
    ResolverResults,
    ResolverStrategySource,
    ResolverWorkItem,
    resolver_work_item_is_executable,
    SearchResults,
    SourceType,
)


DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "resolver_system_v2.md"
)
DEFAULT_MAX_FOLLOW_UPS = 30
DEFAULT_MAX_SOURCE_ACTIONS = 10
DEFAULT_MAX_SEARCH_TASKS = 5
DEFAULT_MAX_QUERIES_PER_ITEM = 3
_TERMINAL_RETRIEVAL_ERROR_CODES = {"access_denied", "anti_bot_page"}
_AUTOMATED_AVAILABILITIES = {
    FieldAvailability.PUBLIC_EXPECTED,
    FieldAvailability.PUBLIC_OPTIONAL,
    FieldAvailability.REGISTRY_EXPECTED,
}
_HUMAN_ONLY_AVAILABILITIES = {
    FieldAvailability.MANUAL_RESEARCH_REQUIRED,
    FieldAvailability.PRIVATE_DOCUMENT_REQUIRED,
    FieldAvailability.CONFIDENTIAL_DEAL_ROOM,
    FieldAvailability.NOT_APPLICABLE,
}


class ResolverValidationError(ValueError):
    """Raised before an invalid or misleading Resolver artifact is saved."""


class ResolverDraftValidationError(ValueError):
    """Describe why a paid Resolver draft could not be grounded locally."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


class ResolverAgent:
    """Create executable routing without claiming that research already ran."""

    def __init__(
        self,
        llm: ResolverLLM | None = None,
        *,
        prompt_path: Path | str = DEFAULT_PROMPT_PATH,
    ) -> None:
        self.llm = llm
        self.prompt_path = Path(prompt_path)

    @staticmethod
    def _profile_policy_index(
        plan: ResearchPlan,
    ) -> dict[tuple[str, str], tuple[FieldAvailability, bool, ProfileReuseScope]]:
        """Resolve immutable profile policy to runtime task/field identifiers."""

        if plan.profile_snapshot is None:
            return {}
        task_by_question = {
            task.catalog_question_id: task for task in plan.tasks
        }
        return {
            (task_by_question[question.question_id].task_id, field.target_field): (
                field.availability,
                field.required_for_completion,
                question.reuse_scope,
            )
            for question in plan.profile_snapshot.questions
            for field in question.fields
        }

    @classmethod
    def _profile_task_policies(
        cls,
        plan: ResearchPlan,
        task_id: str,
    ) -> list[tuple[str, FieldAvailability, bool, ProfileReuseScope]]:
        task = next(task for task in plan.tasks if task.task_id == task_id)
        index = cls._profile_policy_index(plan)
        return [
            (target_field, *index[(task_id, target_field)])
            for target_field in task.target_fields
            if (task_id, target_field) in index
        ]

    @classmethod
    def _follow_up_policy(
        cls,
        plan: ResearchPlan | None,
        follow_up: CheckerFollowUpTask,
    ) -> tuple[FieldAvailability | None, bool | None, ProfileReuseScope | None]:
        declared = (
            follow_up.availability,
            follow_up.required_for_completion,
            follow_up.reuse_scope,
        )
        if plan is None or plan.profile_snapshot is None:
            return declared
        expected = cls._profile_policy_index(plan).get(
            (follow_up.task_id, follow_up.target_field)
        )
        if expected is None:
            # Resolver creates synthetic task/source-scope follow-ups itself. Their
            # routing metadata is already derived from the immutable profile.
            return declared
        if any(value is not None for value in declared) and declared != expected:
            raise ResolverValidationError(
                "Checker follow-up profile policy differs from the Plan snapshot."
            )
        return expected

    @classmethod
    def _follow_up_route_rank(
        cls,
        plan: ResearchPlan,
        follow_up: CheckerFollowUpTask,
    ) -> tuple[int, int]:
        availability, required, _ = cls._follow_up_policy(plan, follow_up)
        if plan.profile_snapshot is None:
            return (0, 0)
        if availability in _AUTOMATED_AVAILABILITIES:
            route_rank = 0
        elif availability == FieldAvailability.SYSTEM_DERIVED:
            route_rank = 1
        else:
            route_rank = 2
        return (0 if required else 1, route_rank)

    @classmethod
    def _locked_action_for_follow_up(
        cls,
        plan: ResearchPlan | None,
        follow_up: CheckerFollowUpTask,
    ) -> ResolverAction | None:
        availability, _, _ = cls._follow_up_policy(plan, follow_up)
        if availability == FieldAvailability.SYSTEM_DERIVED:
            return ResolverAction.LOCAL_AUDIT
        if (
            follow_up.route == CheckerFollowUpRoute.HUMAN_REVIEW
            or availability in _HUMAN_ONLY_AVAILABILITIES
        ):
            return ResolverAction.HUMAN_REVIEW
        return None

    def create_resolution_results(
        self,
        plan: ResearchPlan,
        search_results: SearchResults,
        extraction_results: ExtractionResults,
        checker_results: CheckerResults,
        *,
        plan_sha256: str,
        search_sha256: str,
        extraction_sha256: str,
        check_sha256: str,
        check_reference: str,
        plan_reference: str | None = None,
        search_reference: str | None = None,
        extraction_reference: str | None = None,
        iteration: int | None = None,
        max_follow_ups: int = DEFAULT_MAX_FOLLOW_UPS,
        max_source_actions: int = DEFAULT_MAX_SOURCE_ACTIONS,
        max_search_tasks: int = DEFAULT_MAX_SEARCH_TASKS,
        max_queries_per_item: int = DEFAULT_MAX_QUERIES_PER_ITEM,
        completed_gap_rounds: int = 0,
        allow_round_limit: bool = False,
        force_scope_expansion: bool = False,
        prefer_new_search: bool = False,
    ) -> ResolverResults:
        resolved_iteration = iteration or checker_results.iteration
        self._validate_inputs(
            plan,
            search_results,
            extraction_results,
            checker_results,
            plan_sha256=plan_sha256,
            search_sha256=search_sha256,
            extraction_sha256=extraction_sha256,
            check_sha256=check_sha256,
            check_reference=check_reference,
            plan_reference=plan_reference,
            search_reference=search_reference,
            extraction_reference=extraction_reference,
            iteration=resolved_iteration,
            max_follow_ups=max_follow_ups,
            max_source_actions=max_source_actions,
            max_search_tasks=max_search_tasks,
            max_queries_per_item=max_queries_per_item,
            force_scope_expansion=force_scope_expansion,
        )
        if completed_gap_rounds < 0:
            raise ResolverValidationError(
                "Completed gap-repair round count cannot be negative."
            )
        if (
            checker_results.recommended_next_action
            == CheckerNextAction.RESOLVE_GAPS
            and completed_gap_rounds >= plan.stop_conditions.max_rounds
            and not allow_round_limit
            and not force_scope_expansion
        ):
            raise ResolverValidationError(
                "Resolver gap-repair limit reached: "
                f"{completed_gap_rounds} completed round(s), plan maximum "
                f"{plan.stop_conditions.max_rounds}. Route the selected scope "
                "to human review or explicitly allow a round-limit override."
            )

        limits = ResolverLimits(
            max_follow_ups=max_follow_ups,
            max_source_actions=max_source_actions,
            max_search_tasks=max_search_tasks,
            max_queries_per_item=max_queries_per_item,
        )
        expanding_scope = force_scope_expansion or (
            checker_results.recommended_next_action
            == CheckerNextAction.RESEARCH_NEXT_BATCH
        )
        source_backlog_expansion = (
            plan.profile_snapshot is None
            and bool(checker_results.unevaluated_source_ids)
        )
        if force_scope_expansion and (
            checker_results.recommended_next_action
            != CheckerNextAction.RESOLVE_GAPS
            or completed_gap_rounds < plan.stop_conditions.max_rounds
        ):
            raise ResolverValidationError(
                "Forced scope expansion requires a resolve_gaps Checker after "
                "the Planner repair-round limit has been exhausted."
            )
        if expanding_scope:
            ordered_follow_ups = self._build_scope_expansion_follow_ups(
                plan,
                search_results,
                checker_results,
            )
            if plan.profile_snapshot is not None:
                expansion_order = {
                    item.follow_up_id: index
                    for index, item in enumerate(ordered_follow_ups)
                }
                ordered_follow_ups = sorted(
                    ordered_follow_ups,
                    key=lambda item: (
                        *self._follow_up_route_rank(plan, item),
                        -PRIORITY_ORDER[item.priority],
                        expansion_order[item.follow_up_id],
                    ),
                )
            expansion_limit = min(
                max_follow_ups,
                (
                    max_source_actions
                    if source_backlog_expansion
                    else max_search_tasks
                ),
            )
            selected_follow_ups = ordered_follow_ups[:expansion_limit]
        else:
            checker_order = {
                item.follow_up_id: index
                for index, item in enumerate(checker_results.follow_up_tasks)
            }
            ordered_follow_ups = sorted(
                checker_results.follow_up_tasks,
                key=lambda item: (
                    *self._follow_up_route_rank(plan, item),
                    -PRIORITY_ORDER[item.priority],
                    checker_order[item.follow_up_id],
                ),
            )
            selected_follow_ups = ordered_follow_ups[:max_follow_ups]
        deferred_follow_up_ids = [
            item.follow_up_id
            for item in ordered_follow_ups[len(selected_follow_ups):]
        ]
        eligible_source_pools = self._eligible_source_pools(
            selected_follow_ups,
            search_results,
            extraction_results,
            plan=plan,
        )
        available_source_ids = self._available_source_ids(
            eligible_source_pools,
            search_results,
        )
        deterministic_items = self._build_deterministic_items(
            plan,
            selected_follow_ups,
            checker_results,
            eligible_source_pools=eligible_source_pools,
            max_source_actions=max_source_actions,
            max_search_tasks=max_search_tasks,
            max_queries_per_item=max_queries_per_item,
            prefer_new_search=prefer_new_search,
        )

        warnings: list[str] = []
        if (
            completed_gap_rounds >= plan.stop_conditions.max_rounds
            and not force_scope_expansion
            and allow_round_limit
        ):
            warnings.append(
                "Resolver round-limit override was explicitly enabled after "
                f"{completed_gap_rounds} completed gap-repair round(s)."
            )
        if expanding_scope:
            if force_scope_expansion:
                warnings.append(
                    "A human explicitly advanced to the next research batch with "
                    "unresolved selected-scope gaps after exhausting the Planner "
                    "repair-round limit; all gaps remain blocking final approval."
                )
            if source_backlog_expansion:
                warnings.append(
                    f"Scheduled {len(selected_follow_ups)} known but unevaluated "
                    "source(s) before expanding to new plan tasks."
                )
            else:
                warnings.append(
                    f"Scheduled {len(selected_follow_ups)} previously unevaluated "
                    "plan task(s) as the next bounded research batch."
                )
        if deferred_follow_up_ids:
            if expanding_scope:
                if source_backlog_expansion:
                    warnings.append(
                        f"Deferred {len(deferred_follow_up_ids)} remaining known "
                        "source(s) to later scope-expansion batches."
                    )
                else:
                    warnings.append(
                        f"Deferred {len(deferred_follow_up_ids)} remaining plan "
                        "task(s) to later scope-expansion batches."
                    )
            else:
                warnings.append(
                    f"Deferred {len(deferred_follow_up_ids)} lower-priority follow-up "
                    "task(s) because max_follow_ups was reached."
                )
        if prefer_new_search:
            warnings.append(
                "The caller explicitly preferred bounded new-source search over "
                "replaying remaining known candidates."
            )
        usage: list[AgentIterationUsage] = []
        failed_attempts: list[ResolverAttemptFailure] = []
        generated_by = "deterministic"
        strategy_source = ResolverStrategySource.DETERMINISTIC
        model = None
        provider_executed = False
        work_items = deterministic_items

        scope_task_ids = _deduplicate(
            [item.task_id for item in deterministic_items]
        )
        deterministic_only = bool(deterministic_items) and all(
            item.selected_action
            in {ResolverAction.LOCAL_AUDIT, ResolverAction.HUMAN_REVIEW}
            for item in deterministic_items
        )
        if self.llm is not None and deterministic_only:
            warnings.append(
                "Resolver skipped its provider call because every selected item "
                "is locked to deterministic local or human work."
            )
        elif self.llm is not None:
            generated_by = "openai"
            model = self.llm.model_name
            provider_executed = True
            try:
                system_prompt = self.prompt_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ResolverValidationError(
                    f"Cannot load Resolver prompt: {self.prompt_path}"
                ) from exc
            try:
                generation = self.llm.generate(
                    plan,
                    search_results,
                    checker_results,
                    deterministic_items,
                    system_prompt,
                    iteration=resolved_iteration,
                    call_index=1,
                )
            except ResolverProviderError as exc:
                if exc.usage is not None:
                    self._validate_usage(
                        exc.usage,
                        iteration=resolved_iteration,
                        scope_task_ids=scope_task_ids,
                        scope_source_ids=available_source_ids,
                    )
                    usage.append(exc.usage)
                failed_attempts.append(
                    ResolverAttemptFailure(
                        call_index=1,
                        scope_task_ids=scope_task_ids,
                        scope_source_ids=available_source_ids,
                        error_code=exc.code,
                        usage_recorded=exc.usage is not None,
                        token_usage_unknown=exc.usage is None,
                    )
                )
                strategy_source = ResolverStrategySource.DETERMINISTIC_FALLBACK
                warnings.append(
                    f"Paid Resolver strategy failed with {exc.code}; retained the "
                    "deterministic executable plan."
                )
            else:
                self._validate_usage(
                    generation.usage,
                    iteration=resolved_iteration,
                    scope_task_ids=scope_task_ids,
                    scope_source_ids=available_source_ids,
                )
                usage.append(generation.usage)
                try:
                    work_items = self._ground_draft(
                        generation.draft,
                        deterministic_items,
                        selected_follow_ups,
                        eligible_source_pools=eligible_source_pools,
                        max_source_actions=max_source_actions,
                        max_search_tasks=max_search_tasks,
                        max_queries_per_item=max_queries_per_item,
                    )
                except Exception as exc:
                    error_code = (
                        exc.code
                        if isinstance(exc, ResolverDraftValidationError)
                        else "invalid_resolver_output"
                    )
                    error_detail = (
                        str(exc)
                        if isinstance(exc, ResolverDraftValidationError)
                        else type(exc).__name__
                    )
                    failed_attempts.append(
                        ResolverAttemptFailure(
                            call_index=1,
                            scope_task_ids=scope_task_ids,
                            scope_source_ids=available_source_ids,
                            error_code=error_code,
                            usage_recorded=True,
                            token_usage_unknown=False,
                        )
                    )
                    strategy_source = ResolverStrategySource.DETERMINISTIC_FALLBACK
                    warnings.append(
                        "Paid Resolver output failed local validation "
                        f"({error_code}: {error_detail}); retained the deterministic "
                        "plan."
                    )
                else:
                    strategy_source = ResolverStrategySource.OPENAI

        work_items = sorted(work_items, key=lambda item: item.sequence)
        selected_follow_up_ids = [item.follow_up_id for item in work_items]
        execution_batches = self._build_batches(work_items)
        execution_source_ids = _deduplicate(
            [
                source_id
                for item in work_items
                for source_id in item.selected_source_ids
            ]
        )
        search_task_ids = _deduplicate(
            [
                item.task_id
                for item in work_items
                if item.selected_action == ResolverAction.SEARCH_NEW_SOURCE
            ]
        )
        compliance_rules = _deduplicate(
            [
                *checker_results.compliance_rules,
                "Resolver plans work only; it never reports a source as fetched, "
                "parsed, or verified.",
                "Prefer known unevaluated sources before retrying blocked sources "
                "or searching broadly.",
                "Do not overwrite prior immutable artifacts; the next round must preserve lineage.",
                "Do not send unresolved data to Normalizer until a new Checker pass accepts it.",
                "Data-quality governance tasks are audited from immutable local "
                "artifacts and never trigger web search or document extraction.",
                "Manual, private-document, confidential, and not-applicable "
                "profile fields never trigger automated sources or queries.",
                "System-derived profile fields are handled only through local "
                "immutable-artifact audits.",
            ]
        )
        result_payload = {
            "resolution_id": str(uuid4()),
            "plan_run_id": plan.run_id,
            "search_id": search_results.search_id,
            "extraction_id": extraction_results.extraction_id,
            "check_id": checker_results.check_id,
            "plan_sha256": plan_sha256,
            "search_sha256": search_sha256,
            "extraction_sha256": extraction_sha256,
            "check_sha256": check_sha256,
            "plan_reference": plan_reference or checker_results.plan_reference,
            "search_reference": search_reference or checker_results.search_reference,
            "extraction_reference": (
                extraction_reference or checker_results.extraction_reference
            ),
            "check_reference": check_reference,
            "created_at": datetime.now(timezone.utc),
            "iteration": resolved_iteration,
            "generated_by": generated_by,
            "strategy_source": strategy_source,
            "model": model,
            "provider_executed": provider_executed,
            "brand_name": plan.planner_input.brand_name,
            "target_country": plan.planner_input.target_country,
            "depth": plan.planner_input.depth,
            "limits": limits,
            "scope_expansion_override": force_scope_expansion,
            "available_source_ids": available_source_ids,
            "selected_follow_up_ids": selected_follow_up_ids,
            "deferred_follow_up_ids": deferred_follow_up_ids,
            "work_items": work_items,
            "execution_batches": execution_batches,
            "execution_source_ids": execution_source_ids,
            "search_task_ids": search_task_ids,
            "ready_for_execution": any(
                resolver_work_item_is_executable(item) for item in work_items
            ),
            "recommended_next_action": (
                ResolverNextAction.EXECUTE_RESOLUTION
                if any(
                    resolver_work_item_is_executable(item)
                    for item in work_items
                )
                else ResolverNextAction.HUMAN_REVIEW
            ),
            "warnings": warnings,
            "compliance_rules": compliance_rules,
            "agent_usage": usage,
            "failed_attempts": failed_attempts,
        }
        try:
            return ResolverResults.model_validate(result_payload)
        except ValueError:
            if generated_by == "openai":
                raise ResolverProviderError(
                    "Paid Resolver result failed final local artifact validation.",
                    code="invalid_resolver_artifact",
                    usage=usage[0] if usage else None,
                    iteration=resolved_iteration,
                    call_index=1,
                    scope_task_ids=scope_task_ids,
                    scope_source_ids=available_source_ids,
                    requested_model=model,
                    failed_attempts=failed_attempts,
                ) from None
            raise ResolverValidationError(
                "Deterministic Resolver result failed final artifact validation."
            ) from None

    @staticmethod
    def _available_source_ids(
        eligible_source_pools: dict[
            str, dict[ResolverAction, list[str]]
        ],
        search_results: SearchResults,
    ) -> list[str]:
        referenced = {
            source_id
            for pools in eligible_source_pools.values()
            for source_ids in pools.values()
            for source_id in source_ids
        }
        return [
            source.source_id
            for source in search_results.sources
            if source.source_id in referenced
        ]

    @classmethod
    def _eligible_source_pools(
        cls,
        follow_ups: list[CheckerFollowUpTask],
        search_results: SearchResults,
        extraction_results: ExtractionResults,
        *,
        plan: ResearchPlan | None = None,
    ) -> dict[str, dict[ResolverAction, list[str]]]:
        """Keep only source actions that can materially change evidence state."""

        source_by_id = {
            source.source_id: source for source in search_results.sources
        }
        usable_document_source_ids = {
            document.source_id
            for document in extraction_results.documents
            if document.retrieval_status == DocumentRetrievalStatus.FETCHED
            and document.parse_status
            in {DocumentParseStatus.PARSED, DocumentParseStatus.PARTIAL}
        }
        retryable_document_source_ids = {
            document.source_id
            for document in extraction_results.documents
            if document.retrieval_status == DocumentRetrievalStatus.FAILED
            or (
                document.retrieval_status
                == DocumentRetrievalStatus.NOT_ACCESSIBLE
                and document.error_code not in _TERMINAL_RETRIEVAL_ERROR_CODES
            )
        }
        materialized_document_source_ids = {
            document.source_id for document in extraction_results.documents
        }
        processed_scopes = {
            (scope.task_id, scope.source_id)
            for scope in extraction_results.semantically_processed_scopes
        }

        def evidence_source_ids(source_ids: list[str]) -> list[str]:
            return [
                source_id
                for source_id in _deduplicate(source_ids)
                if source_id in source_by_id
                and source_by_id[source_id].source_type
                != SourceType.ROUTING_LEAD
            ]

        pools_by_follow_up: dict[
            str, dict[ResolverAction, list[str]]
        ] = {}
        for follow_up in follow_ups:
            if cls._locked_action_for_follow_up(plan, follow_up) is not None:
                pools_by_follow_up[follow_up.follow_up_id] = {
                    ResolverAction.EXTRACT_KNOWN_SOURCE: [],
                    ResolverAction.RETRY_RETRIEVAL: [],
                    ResolverAction.REEXTRACT_EXISTING: [],
                }
                continue
            reextract_source_ids = [
                source_id
                for source_id in evidence_source_ids(
                    follow_up.reextract_source_ids
                )
                if source_id in usable_document_source_ids
                and (follow_up.task_id, source_id) not in processed_scopes
            ]
            pools_by_follow_up[follow_up.follow_up_id] = {
                # A source that already has a document in the immutable
                # Extractor state is not "known but unevaluated". Scheduling
                # EXTRACT_KNOWN_SOURCE for it merely replays the same document
                # and can prevent a genuine gap search from running.
                ResolverAction.EXTRACT_KNOWN_SOURCE: [
                    source_id
                    for source_id in evidence_source_ids(
                        follow_up.candidate_source_ids
                    )
                    if source_id not in materialized_document_source_ids
                ],
                ResolverAction.RETRY_RETRIEVAL: evidence_source_ids(
                    follow_up.retry_source_ids
                ),
                ResolverAction.REEXTRACT_EXISTING: reextract_source_ids,
            }
            pools_by_follow_up[follow_up.follow_up_id][
                ResolverAction.RETRY_RETRIEVAL
            ] = [
                source_id
                for source_id in pools_by_follow_up[follow_up.follow_up_id][
                    ResolverAction.RETRY_RETRIEVAL
                ]
                if source_id in retryable_document_source_ids
            ]
        return pools_by_follow_up

    @classmethod
    def _build_scope_expansion_follow_ups(
        cls,
        plan: ResearchPlan,
        search_results: SearchResults,
        checker_results: CheckerResults,
    ) -> list[CheckerFollowUpTask]:
        task_by_id = {task.task_id: task for task in plan.tasks}

        def scope_policy(task_id: str):
            policies = cls._profile_task_policies(plan, task_id)
            automated = [
                item for item in policies if item[1] in _AUTOMATED_AVAILABILITIES
            ]
            system = [
                item
                for item in policies
                if item[1] == FieldAvailability.SYSTEM_DERIVED
            ]
            if automated:
                # A single public search can efficiently serve all automatable
                # fields in a mixed task. Searcher still applies its own field
                # boundary before any web-enabled model call.
                return sorted(
                    automated,
                    key=lambda item: (
                        not item[2],
                        item[1] == FieldAvailability.PUBLIC_OPTIONAL,
                    ),
                )[0]
            if system:
                return system[0]
            return policies[0] if policies else None

        # Profile Checker 1.5 keeps an evidence backlog separately from the
        # profile completion scope. Scope expansion must advance profile tasks;
        # replaying every historical source first can starve L2/L3 progression.
        # Legacy artifacts retain their established source-first behavior.
        if (
            plan.profile_snapshot is None
            and checker_results.unevaluated_source_ids
        ):
            source_by_id = {
                source.source_id: source for source in search_results.sources
            }
            selected_task_ids = set(checker_results.selected_task_ids)
            follow_ups: list[CheckerFollowUpTask] = []
            for source_id in checker_results.unevaluated_source_ids:
                source = source_by_id[source_id]
                task_id = next(
                    (
                        item
                        for item in source.task_ids
                        if item in selected_task_ids
                    ),
                    source.task_ids[0],
                )
                task = task_by_id[task_id]
                policy = scope_policy(task.task_id)
                availability = policy[1] if policy is not None else None
                required = policy[2] if policy is not None else None
                reuse_scope = policy[3] if policy is not None else None
                local_audit = (
                    availability == FieldAvailability.SYSTEM_DERIVED
                    or (policy is None and task.section_id == "data_quality")
                )
                human_review = availability in _HUMAN_ONLY_AVAILABILITIES
                profile_action = (
                    CheckerFollowUpAction.LOCAL_AUDIT
                    if local_audit
                    else CheckerFollowUpAction.REQUEST_AUTHORIZED_DOCUMENT
                    if availability
                    in {
                        FieldAvailability.PRIVATE_DOCUMENT_REQUIRED,
                        FieldAvailability.CONFIDENTIAL_DEAL_ROOM,
                    }
                    else CheckerFollowUpAction.MANUAL_RESEARCH
                    if human_review
                    else CheckerFollowUpAction.EXTRACT_KNOWN_SOURCE
                )
                follow_ups.append(
                    CheckerFollowUpTask(
                        follow_up_id=_stable_id(
                            "followup", "source-expansion", source.source_id
                        ),
                        task_id=task.task_id,
                        target_field="__source_scope__",
                        availability=availability,
                        required_for_completion=required,
                        reuse_scope=reuse_scope,
                        priority=task.priority,
                        reason=CheckerFollowUpReason.SOURCE_NOT_EVALUATED,
                        question=(
                            "Extract and evaluate the known Searcher source for "
                            f"task '{task.title}': {source.canonical_url}"
                        ),
                        required_source_types=(
                            []
                            if local_audit or human_review
                            else [source.source_type]
                        ),
                        route=(
                            CheckerFollowUpRoute.HUMAN_REVIEW
                            if human_review
                            else CheckerFollowUpRoute.RESOLVER
                        ),
                        action=profile_action,
                        candidate_source_ids=(
                            [] if local_audit or human_review else [source.source_id]
                        ),
                        minimum_additional_sources=0,
                        requires_independent_source=False,
                        suggested_queries=(
                            []
                            if local_audit or human_review
                            else _deduplicate(task.search_queries)[:10]
                        ),
                        completion_criteria=(
                            "Complete when the profile field is handled in the "
                            "human research workflow."
                            if human_review
                            else "Complete when local immutable artifacts derive "
                            "the system-owned field without external evidence calls."
                            if local_audit
                            else "Complete when the known source has a retrieval and "
                            "extraction result and a new Checker pass evaluates it."
                        ),
                    )
                )
            return follow_ups

        follow_ups: list[CheckerFollowUpTask] = []
        for task_id in checker_results.unevaluated_task_ids:
            task = task_by_id[task_id]
            policy = scope_policy(task.task_id)
            availability = policy[1] if policy is not None else None
            required = policy[2] if policy is not None else None
            reuse_scope = policy[3] if policy is not None else None
            local_audit = (
                availability == FieldAvailability.SYSTEM_DERIVED
                or (policy is None and task.section_id == "data_quality")
            )
            human_review = availability in _HUMAN_ONLY_AVAILABILITIES
            profile_action = (
                CheckerFollowUpAction.LOCAL_AUDIT
                if local_audit
                else CheckerFollowUpAction.REQUEST_AUTHORIZED_DOCUMENT
                if availability
                in {
                    FieldAvailability.PRIVATE_DOCUMENT_REQUIRED,
                    FieldAvailability.CONFIDENTIAL_DEAL_ROOM,
                }
                else CheckerFollowUpAction.MANUAL_RESEARCH
                if human_review
                else CheckerFollowUpAction.FIND_ALTERNATIVE_SOURCE
            )
            queries = (
                []
                if local_audit or human_review
                else _deduplicate(task.search_queries)
            )
            if not local_audit and not human_review and not queries:
                queries = [
                    f'"{plan.planner_input.brand_name}" {task.title} '
                    f"{plan.planner_input.target_country}"
                ]
            follow_ups.append(
                CheckerFollowUpTask(
                    follow_up_id=_stable_id(
                        "followup", "scope-expansion", task.task_id
                    ),
                    task_id=task.task_id,
                    target_field="__task_scope__",
                    availability=availability,
                    required_for_completion=required,
                    reuse_scope=reuse_scope,
                    priority=task.priority,
                    reason=CheckerFollowUpReason.SCOPE_NOT_STARTED,
                    question=(
                        "Research the previously unevaluated plan task: "
                        f"{task.question}"
                    ),
                    required_source_types=(
                        []
                        if local_audit or human_review
                        else task.preferred_source_types
                    ),
                    related_claim_ids=[],
                    supporting_claim_ids=[],
                    route=(
                        CheckerFollowUpRoute.HUMAN_REVIEW
                        if human_review
                        else CheckerFollowUpRoute.RESOLVER
                    ),
                    action=profile_action,
                    candidate_source_ids=[],
                    retry_source_ids=[],
                    reextract_source_ids=[],
                    minimum_additional_sources=(
                        0 if local_audit or human_review else task.min_sources
                    ),
                    requires_independent_source=(
                        False
                        if local_audit or human_review
                        else task.requires_independent_corroboration
                    ),
                    suggested_queries=queries[:10],
                    completion_criteria=(
                        "Complete when the local Checker audit derives every quality "
                        "field from immutable plan, search, extraction, and checker "
                        "artifacts without external evidence calls."
                        if local_audit
                        else "Complete when the task enters the human research "
                        "workflow without an automated web or extraction call."
                        if human_review
                        else "Complete when this plan task has been searched, its "
                        "mapped sources have been extracted, and a new Checker pass "
                        "has evaluated every target field."
                    ),
                )
            )
        return follow_ups

    @staticmethod
    def _field_issue_codes(
        checker_results: CheckerResults,
    ) -> dict[tuple[str, str], set[CheckerIssueCode]]:
        return {
            (task.task_id, field.target_field): set(field.issue_codes)
            for task in checker_results.task_results
            for field in task.field_results
        }

    @classmethod
    def _build_deterministic_items(
        cls,
        plan: ResearchPlan,
        follow_ups: list[CheckerFollowUpTask],
        checker_results: CheckerResults,
        *,
        eligible_source_pools: dict[
            str, dict[ResolverAction, list[str]]
        ],
        max_source_actions: int,
        max_search_tasks: int,
        max_queries_per_item: int,
        prefer_new_search: bool = False,
    ) -> list[ResolverWorkItem]:
        issue_codes = cls._field_issue_codes(checker_results)
        allocated_sources: list[str] = []
        allocated_search_tasks: list[str] = []
        items: list[ResolverWorkItem] = []

        def take_sources(source_ids: list[str]) -> list[str]:
            selected: list[str] = []
            for source_id in source_ids:
                if source_id in allocated_sources:
                    selected.append(source_id)
                elif len(allocated_sources) < max_source_actions:
                    allocated_sources.append(source_id)
                    selected.append(source_id)
            return selected

        for sequence, follow_up in enumerate(follow_ups, start=1):
            availability, required, reuse_scope = cls._follow_up_policy(
                plan, follow_up
            )
            locked_action = cls._locked_action_for_follow_up(plan, follow_up)
            if locked_action is not None:
                items.append(
                    ResolverWorkItem(
                        resolution_item_id=_stable_id(
                            "resolution-item", follow_up.follow_up_id
                        ),
                        follow_up_id=follow_up.follow_up_id,
                        task_id=follow_up.task_id,
                        target_field=follow_up.target_field,
                        field_availability=availability,
                        required_for_completion=required,
                        reuse_scope=reuse_scope,
                        priority=follow_up.priority,
                        reason=follow_up.reason,
                        sequence=sequence,
                        allowed_actions=[locked_action],
                        selected_action=locked_action,
                        selected_source_ids=[],
                        fallback_source_ids=[],
                        queries=[],
                        related_claim_ids=follow_up.related_claim_ids,
                        supporting_claim_ids=follow_up.supporting_claim_ids,
                        minimum_additional_sources=0,
                        requires_independent_source=False,
                        completion_criteria=follow_up.completion_criteria,
                        rationale=(
                            "Profile availability requires deterministic local "
                            "derivation without external calls."
                            if locked_action == ResolverAction.LOCAL_AUDIT
                            else "Profile availability or Checker routing requires "
                            "human work without automated sources or queries."
                        ),
                    )
                )
                continue
            if follow_up.reason == CheckerFollowUpReason.SCOPE_NOT_STARTED:
                if follow_up.action in {
                    CheckerFollowUpAction.SEMANTIC_REVIEW,
                    CheckerFollowUpAction.LOCAL_AUDIT,
                }:
                    items.append(
                        ResolverWorkItem(
                            resolution_item_id=_stable_id(
                                "resolution-item", follow_up.follow_up_id
                            ),
                            follow_up_id=follow_up.follow_up_id,
                            task_id=follow_up.task_id,
                            target_field=follow_up.target_field,
                            field_availability=availability,
                            required_for_completion=required,
                            reuse_scope=reuse_scope,
                            priority=follow_up.priority,
                            reason=follow_up.reason,
                            sequence=sequence,
                            allowed_actions=[ResolverAction.LOCAL_AUDIT],
                            selected_action=ResolverAction.LOCAL_AUDIT,
                            selected_source_ids=[],
                            fallback_source_ids=[],
                            queries=[],
                            related_claim_ids=[],
                            supporting_claim_ids=[],
                            minimum_additional_sources=0,
                            requires_independent_source=False,
                            completion_criteria=follow_up.completion_criteria,
                            rationale=(
                                "Data-quality policy is derived from local immutable "
                                "artifacts and must not trigger web research."
                            ),
                        )
                    )
                    continue
                queries = _deduplicate(follow_up.suggested_queries)[
                    :max_queries_per_item
                ]
                items.append(
                    ResolverWorkItem(
                        resolution_item_id=_stable_id(
                            "resolution-item", follow_up.follow_up_id
                        ),
                        follow_up_id=follow_up.follow_up_id,
                        task_id=follow_up.task_id,
                        target_field=follow_up.target_field,
                        field_availability=availability,
                        required_for_completion=required,
                        reuse_scope=reuse_scope,
                        priority=follow_up.priority,
                        reason=follow_up.reason,
                        sequence=sequence,
                        allowed_actions=[ResolverAction.SEARCH_NEW_SOURCE],
                        selected_action=ResolverAction.SEARCH_NEW_SOURCE,
                        selected_source_ids=[],
                        fallback_source_ids=[],
                        queries=queries,
                        related_claim_ids=[],
                        supporting_claim_ids=[],
                        minimum_additional_sources=(
                            follow_up.minimum_additional_sources
                        ),
                        requires_independent_source=(
                            follow_up.requires_independent_source
                        ),
                        completion_criteria=follow_up.completion_criteria,
                        rationale=(
                            "The prior selected scope passed its local gate; the next "
                            "plan task requires a bounded new-source search."
                        ),
                    )
                )
                allocated_search_tasks.append(follow_up.task_id)
                continue
            field_issues = issue_codes.get(
                (follow_up.task_id, follow_up.target_field), set()
            )
            mentioned_not_obtained = (
                CheckerIssueCode.MENTIONED_NOT_OBTAINED in field_issues
            )
            pools = {
                action: list(source_ids)
                for action, source_ids in eligible_source_pools[
                    follow_up.follow_up_id
                ].items()
            }
            if mentioned_not_obtained:
                pools[ResolverAction.REEXTRACT_EXISTING] = []
            allowed_actions = [
                action for action, source_ids in pools.items() if source_ids
            ]
            allowed_actions.append(ResolverAction.SEARCH_NEW_SOURCE)
            allowed_actions.append(ResolverAction.HUMAN_REVIEW)

            preferred_actions: list[ResolverAction] = []
            if prefer_new_search:
                preferred_actions.append(ResolverAction.SEARCH_NEW_SOURCE)
            if follow_up.candidate_source_ids:
                if pools[ResolverAction.EXTRACT_KNOWN_SOURCE]:
                    preferred_actions.append(
                        ResolverAction.EXTRACT_KNOWN_SOURCE
                    )
            if mentioned_not_obtained and follow_up.retry_source_ids:
                if pools[ResolverAction.RETRY_RETRIEVAL]:
                    preferred_actions.append(ResolverAction.RETRY_RETRIEVAL)
            requires_new_evidence = (
                follow_up.minimum_additional_sources > 0
                or follow_up.requires_independent_source
                or follow_up.action
                in {
                    CheckerFollowUpAction.CORROBORATE,
                    CheckerFollowUpAction.FIND_ALTERNATIVE_SOURCE,
                    CheckerFollowUpAction.RESOLVE_CONFLICT,
                }
                or bool(
                    field_issues
                    & {
                        CheckerIssueCode.INSUFFICIENT_SOURCES,
                        CheckerIssueCode.NEEDS_INDEPENDENT_CORROBORATION,
                        CheckerIssueCode.PREFERRED_SOURCE_MISSING,
                        CheckerIssueCode.SELF_DECLARATION_ONLY,
                    }
                )
            )
            if requires_new_evidence:
                preferred_actions.append(ResolverAction.SEARCH_NEW_SOURCE)
            if (
                not mentioned_not_obtained
                and pools[ResolverAction.REEXTRACT_EXISTING]
            ):
                preferred_actions.append(ResolverAction.REEXTRACT_EXISTING)
            if pools[ResolverAction.RETRY_RETRIEVAL]:
                preferred_actions.append(ResolverAction.RETRY_RETRIEVAL)
            preferred_actions.append(ResolverAction.SEARCH_NEW_SOURCE)
            if ResolverAction.HUMAN_REVIEW in allowed_actions:
                preferred_actions.append(ResolverAction.HUMAN_REVIEW)

            selected_action = ResolverAction.HUMAN_REVIEW
            selected_sources: list[str] = []
            for action in _deduplicate([item.value for item in preferred_actions]):
                candidate_action = ResolverAction(action)
                if candidate_action in pools:
                    selected_sources = take_sources(pools[candidate_action])
                    if selected_sources:
                        selected_action = candidate_action
                        break
                elif candidate_action == ResolverAction.SEARCH_NEW_SOURCE:
                    if (
                        follow_up.task_id in allocated_search_tasks
                        or len(allocated_search_tasks) < max_search_tasks
                    ):
                        if follow_up.task_id not in allocated_search_tasks:
                            allocated_search_tasks.append(follow_up.task_id)
                        selected_action = candidate_action
                        break
                else:
                    selected_action = candidate_action
                    break

            all_source_ids = _deduplicate(
                [
                    *pools[ResolverAction.EXTRACT_KNOWN_SOURCE],
                    *pools[ResolverAction.RETRY_RETRIEVAL],
                    *pools[ResolverAction.REEXTRACT_EXISTING],
                ]
            )
            fallback_source_ids = [
                source_id
                for source_id in all_source_ids
                if source_id not in selected_sources
            ]
            queries = (
                []
                if selected_action == ResolverAction.HUMAN_REVIEW
                else _deduplicate(follow_up.suggested_queries)[
                    :max_queries_per_item
                ]
            )
            if selected_action == ResolverAction.HUMAN_REVIEW:
                fallback_source_ids = []
            items.append(
                ResolverWorkItem(
                    resolution_item_id=_stable_id(
                        "resolution-item", follow_up.follow_up_id
                    ),
                    follow_up_id=follow_up.follow_up_id,
                    task_id=follow_up.task_id,
                    target_field=follow_up.target_field,
                    field_availability=availability,
                    required_for_completion=required,
                    reuse_scope=reuse_scope,
                    priority=follow_up.priority,
                    reason=follow_up.reason,
                    sequence=sequence,
                    allowed_actions=allowed_actions,
                    selected_action=selected_action,
                    selected_source_ids=selected_sources,
                    fallback_source_ids=fallback_source_ids,
                    queries=queries,
                    related_claim_ids=follow_up.related_claim_ids,
                    supporting_claim_ids=follow_up.supporting_claim_ids,
                    minimum_additional_sources=(
                        follow_up.minimum_additional_sources
                    ),
                    requires_independent_source=(
                        follow_up.requires_independent_source
                    ),
                    completion_criteria=follow_up.completion_criteria,
                    rationale=(
                        "Deterministic routing selected an evidence-producing "
                        "action within the source and search budgets."
                    ),
                )
            )
        return items

    @classmethod
    def _ground_draft(
        cls,
        draft: ResolverDraft,
        deterministic_items: list[ResolverWorkItem],
        follow_ups: list[CheckerFollowUpTask],
        *,
        eligible_source_pools: dict[
            str, dict[ResolverAction, list[str]]
        ],
        max_source_actions: int,
        max_search_tasks: int,
        max_queries_per_item: int,
    ) -> list[ResolverWorkItem]:
        expected_ids = [item.follow_up_id for item in deterministic_items]
        if set(item.follow_up_id for item in draft.items) != set(expected_ids):
            raise ResolverDraftValidationError(
                "The draft must cover every selected follow-up exactly once.",
                code="incomplete_follow_up_coverage",
            )
        deterministic_by_id = {
            item.follow_up_id: item for item in deterministic_items
        }
        follow_up_by_id = {item.follow_up_id: item for item in follow_ups}
        source_pool_by_action = {
            ResolverAction.EXTRACT_KNOWN_SOURCE: "candidate_source_ids",
            ResolverAction.RETRY_RETRIEVAL: "retry_source_ids",
            ResolverAction.REEXTRACT_EXISTING: "reextract_source_ids",
        }
        execution_sources: list[str] = []
        search_tasks: list[str] = []
        grounded: list[ResolverWorkItem] = []
        for item in sorted(draft.items, key=lambda value: value.sequence):
            baseline = deterministic_by_id[item.follow_up_id]
            follow_up = follow_up_by_id[item.follow_up_id]
            if item.selected_action not in baseline.allowed_actions:
                raise ResolverDraftValidationError(
                    "The draft selected a locally forbidden action.",
                    code="forbidden_action",
                )
            source_field = source_pool_by_action.get(item.selected_action)
            allowed_source_ids = list(
                eligible_source_pools[follow_up.follow_up_id].get(
                    item.selected_action, []
                )
            )
            if source_field:
                if (
                    not item.selected_source_ids
                    or not set(item.selected_source_ids).issubset(allowed_source_ids)
                ):
                    raise ResolverDraftValidationError(
                        "The draft selected an ineligible source for its action.",
                        code="invalid_source_selection",
                    )
            elif item.selected_source_ids:
                raise ResolverDraftValidationError(
                    "A non-source action cannot select source IDs.",
                    code="unexpected_source_selection",
                )
            for source_id in item.selected_source_ids:
                if source_id not in execution_sources:
                    execution_sources.append(source_id)
            if len(execution_sources) > max_source_actions:
                raise ResolverDraftValidationError(
                    "The draft exceeds the source-action budget.",
                    code="source_action_budget_exceeded",
                )
            if item.selected_action == ResolverAction.SEARCH_NEW_SOURCE:
                if baseline.task_id not in search_tasks:
                    search_tasks.append(baseline.task_id)
                if len(search_tasks) > max_search_tasks:
                    raise ResolverDraftValidationError(
                        "The draft exceeds the search-task budget.",
                        code="search_task_budget_exceeded",
                    )
            queries = (
                []
                if item.selected_action
                in {ResolverAction.HUMAN_REVIEW, ResolverAction.LOCAL_AUDIT}
                else _deduplicate(
                    [*item.derived_queries, *baseline.queries]
                )[:max_queries_per_item]
            )
            fallback_source_ids = [
                source_id
                for source_id in _deduplicate(
                    [
                        *eligible_source_pools[follow_up.follow_up_id][
                            ResolverAction.EXTRACT_KNOWN_SOURCE
                        ],
                        *eligible_source_pools[follow_up.follow_up_id][
                            ResolverAction.RETRY_RETRIEVAL
                        ],
                        *eligible_source_pools[follow_up.follow_up_id][
                            ResolverAction.REEXTRACT_EXISTING
                        ],
                    ]
                )
                if source_id not in item.selected_source_ids
            ]
            if item.selected_action in {
                ResolverAction.HUMAN_REVIEW,
                ResolverAction.LOCAL_AUDIT,
            }:
                fallback_source_ids = []
            grounded_payload = baseline.model_dump(mode="python")
            grounded_payload.update(
                {
                    "sequence": item.sequence,
                    "selected_action": item.selected_action,
                    "selected_source_ids": item.selected_source_ids,
                    "fallback_source_ids": fallback_source_ids,
                    "queries": queries,
                    "rationale": item.rationale,
                }
            )
            grounded.append(ResolverWorkItem.model_validate(grounded_payload))
        return grounded

    @staticmethod
    def _build_batches(
        work_items: list[ResolverWorkItem],
    ) -> list[ResolverExecutionBatch]:
        grouped: dict[ResolverAction, list[ResolverWorkItem]] = defaultdict(list)
        for item in work_items:
            grouped[item.selected_action].append(item)
        return [
            ResolverExecutionBatch(
                batch_id=_stable_id(
                    "resolution-batch",
                    action.value,
                    *(item.resolution_item_id for item in items),
                ),
                action=action,
                resolution_item_ids=[item.resolution_item_id for item in items],
                follow_up_ids=[item.follow_up_id for item in items],
                task_ids=_deduplicate([item.task_id for item in items]),
                source_ids=_deduplicate(
                    [
                        source_id
                        for item in items
                        for source_id in item.selected_source_ids
                    ]
                ),
                queries=_deduplicate(
                    [query for item in items for query in item.queries]
                ),
            )
            for action, items in grouped.items()
        ]

    @staticmethod
    def _validate_usage(
        usage: AgentIterationUsage,
        *,
        iteration: int,
        scope_task_ids: list[str],
        scope_source_ids: list[str],
    ) -> None:
        if (
            usage.agent != "resolver"
            or usage.iteration != iteration
            or usage.call_index != 1
            or usage.scope_task_ids != scope_task_ids
            or usage.scope_source_ids != scope_source_ids
            or usage.tool_usage
        ):
            raise ResolverValidationError("Resolver provider usage scope is invalid.")

    @staticmethod
    def _validate_inputs(
        plan: ResearchPlan,
        search_results: SearchResults,
        extraction_results: ExtractionResults,
        checker_results: CheckerResults,
        *,
        plan_sha256: str,
        search_sha256: str,
        extraction_sha256: str,
        check_sha256: str,
        check_reference: str,
        plan_reference: str | None,
        search_reference: str | None,
        extraction_reference: str | None,
        iteration: int,
        max_follow_ups: int,
        max_source_actions: int,
        max_search_tasks: int,
        max_queries_per_item: int,
        force_scope_expansion: bool,
    ) -> None:
        for value, label in (
            (plan_sha256, "Plan"),
            (search_sha256, "Searcher"),
            (extraction_sha256, "Extractor"),
            (check_sha256, "Checker"),
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ResolverValidationError(
                    f"{label} artifact SHA-256 must be lowercase hexadecimal."
                )
        if checker_results.generated_by != "openai" or checker_results.failed_attempts:
            raise ResolverValidationError(
                "Resolver requires a successful paid Checker artifact."
            )
        repair_ready = (
            not checker_results.passed
            and checker_results.recommended_next_action
            == CheckerNextAction.RESOLVE_GAPS
            and bool(checker_results.follow_up_tasks)
        )
        expansion_ready = (
            not checker_results.passed
            and checker_results.recommended_next_action
            == CheckerNextAction.RESEARCH_NEXT_BATCH
            and checker_results.selected_scope_ready
            and bool(
                checker_results.unevaluated_task_ids
                or checker_results.unevaluated_source_ids
            )
        )
        forced_expansion_ready = (
            force_scope_expansion
            and not checker_results.passed
            and checker_results.recommended_next_action
            == CheckerNextAction.RESOLVE_GAPS
            and bool(
                checker_results.unevaluated_task_ids
                or checker_results.unevaluated_source_ids
            )
        )
        if not (repair_ready or expansion_ready or forced_expansion_ready):
            raise ResolverValidationError(
                "Checker artifact contains neither repair work nor a ready "
                "scope-expansion batch for Resolver."
            )
        if (
            plan.run_id != checker_results.plan_run_id
            or search_results.search_id != checker_results.search_id
            or extraction_results.extraction_id != checker_results.extraction_id
            or search_results.plan_run_id != plan.run_id
            or extraction_results.plan_run_id != plan.run_id
            or extraction_results.search_id != search_results.search_id
        ):
            raise ResolverValidationError("Resolver input IDs break artifact lineage.")
        if (
            checker_results.plan_sha256 != plan_sha256
            or checker_results.search_sha256 != search_sha256
            or checker_results.extraction_sha256 != extraction_sha256
            or extraction_results.plan_sha256 != plan_sha256
            or extraction_results.search_sha256 != search_sha256
            or search_results.plan_sha256 != plan_sha256
        ):
            raise ResolverValidationError("Resolver input hashes break artifact lineage.")
        for supplied, recorded, label in (
            (plan_reference, checker_results.plan_reference, "Plan"),
            (search_reference, checker_results.search_reference, "Searcher"),
            (
                extraction_reference,
                checker_results.extraction_reference,
                "Extractor",
            ),
        ):
            if supplied is not None and supplied != recorded:
                raise ResolverValidationError(
                    f"{label} reference differs from the Checker lineage."
                )
        if not check_reference.strip():
            raise ResolverValidationError("Checker reference cannot be empty.")
        if iteration < 1:
            raise ResolverValidationError("Resolver iteration must be positive.")
        try:
            ResolverLimits(
                max_follow_ups=max_follow_ups,
                max_source_actions=max_source_actions,
                max_search_tasks=max_search_tasks,
                max_queries_per_item=max_queries_per_item,
            )
        except ValueError as exc:
            raise ResolverValidationError(str(exc)) from exc
        known_task_ids = {task.task_id for task in plan.tasks}
        known_source_ids = {source.source_id for source in search_results.sources}
        if any(
            follow_up.task_id not in known_task_ids
            or not set(
                [
                    *follow_up.candidate_source_ids,
                    *follow_up.retry_source_ids,
                    *follow_up.reextract_source_ids,
                ]
            ).issubset(known_source_ids)
            for follow_up in checker_results.follow_up_tasks
        ):
            raise ResolverValidationError(
                "Checker follow-up references unknown tasks or sources."
            )
