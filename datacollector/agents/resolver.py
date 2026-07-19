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
    CheckerFollowUpTask,
    CheckerIssueCode,
    CheckerNextAction,
    CheckerResults,
    ExtractionResults,
    PRIORITY_ORDER,
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
    SearchResults,
)


DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "resolver_system_v1.md"
)
DEFAULT_MAX_FOLLOW_UPS = 30
DEFAULT_MAX_SOURCE_ACTIONS = 10
DEFAULT_MAX_SEARCH_TASKS = 5
DEFAULT_MAX_QUERIES_PER_ITEM = 3


class ResolverValidationError(ValueError):
    """Raised before an invalid or misleading Resolver artifact is saved."""


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
        )

        limits = ResolverLimits(
            max_follow_ups=max_follow_ups,
            max_source_actions=max_source_actions,
            max_search_tasks=max_search_tasks,
            max_queries_per_item=max_queries_per_item,
        )
        checker_order = {
            item.follow_up_id: index
            for index, item in enumerate(checker_results.follow_up_tasks)
        }
        ordered_follow_ups = sorted(
            checker_results.follow_up_tasks,
            key=lambda item: (
                -PRIORITY_ORDER[item.priority],
                checker_order[item.follow_up_id],
            ),
        )
        selected_follow_ups = ordered_follow_ups[:max_follow_ups]
        deferred_follow_up_ids = [
            item.follow_up_id for item in ordered_follow_ups[max_follow_ups:]
        ]
        available_source_ids = self._available_source_ids(
            selected_follow_ups,
            search_results,
        )
        deterministic_items = self._build_deterministic_items(
            selected_follow_ups,
            checker_results,
            max_source_actions=max_source_actions,
            max_search_tasks=max_search_tasks,
            max_queries_per_item=max_queries_per_item,
        )

        warnings: list[str] = []
        if deferred_follow_up_ids:
            warnings.append(
                f"Deferred {len(deferred_follow_up_ids)} lower-priority follow-up "
                "task(s) because max_follow_ups was reached."
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
        if self.llm is not None:
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
                        max_source_actions=max_source_actions,
                        max_search_tasks=max_search_tasks,
                        max_queries_per_item=max_queries_per_item,
                    )
                except Exception:
                    failed_attempts.append(
                        ResolverAttemptFailure(
                            call_index=1,
                            scope_task_ids=scope_task_ids,
                            scope_source_ids=available_source_ids,
                            error_code="invalid_resolver_output",
                            usage_recorded=True,
                            token_usage_unknown=False,
                        )
                    )
                    strategy_source = ResolverStrategySource.DETERMINISTIC_FALLBACK
                    warnings.append(
                        "Paid Resolver output failed local action, source, budget, "
                        "or exact-coverage validation; retained the deterministic plan."
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
            ]
        )
        return ResolverResults(
            resolution_id=str(uuid4()),
            plan_run_id=plan.run_id,
            search_id=search_results.search_id,
            extraction_id=extraction_results.extraction_id,
            check_id=checker_results.check_id,
            plan_sha256=plan_sha256,
            search_sha256=search_sha256,
            extraction_sha256=extraction_sha256,
            check_sha256=check_sha256,
            plan_reference=plan_reference or checker_results.plan_reference,
            search_reference=search_reference or checker_results.search_reference,
            extraction_reference=(
                extraction_reference or checker_results.extraction_reference
            ),
            check_reference=check_reference,
            created_at=datetime.now(timezone.utc),
            iteration=resolved_iteration,
            generated_by=generated_by,
            strategy_source=strategy_source,
            model=model,
            provider_executed=provider_executed,
            brand_name=plan.planner_input.brand_name,
            target_country=plan.planner_input.target_country,
            depth=plan.planner_input.depth,
            limits=limits,
            available_source_ids=available_source_ids,
            selected_follow_up_ids=selected_follow_up_ids,
            deferred_follow_up_ids=deferred_follow_up_ids,
            work_items=work_items,
            execution_batches=execution_batches,
            execution_source_ids=execution_source_ids,
            search_task_ids=search_task_ids,
            ready_for_execution=bool(work_items),
            recommended_next_action=(
                ResolverNextAction.EXECUTE_RESOLUTION
                if work_items
                else ResolverNextAction.HUMAN_REVIEW
            ),
            warnings=warnings,
            compliance_rules=compliance_rules,
            agent_usage=usage,
            failed_attempts=failed_attempts,
        )

    @staticmethod
    def _available_source_ids(
        follow_ups: list[CheckerFollowUpTask],
        search_results: SearchResults,
    ) -> list[str]:
        referenced = {
            source_id
            for follow_up in follow_ups
            for source_id in [
                *follow_up.candidate_source_ids,
                *follow_up.retry_source_ids,
                *follow_up.reextract_source_ids,
            ]
        }
        return [
            source.source_id
            for source in search_results.sources
            if source.source_id in referenced
        ]

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
        follow_ups: list[CheckerFollowUpTask],
        checker_results: CheckerResults,
        *,
        max_source_actions: int,
        max_search_tasks: int,
        max_queries_per_item: int,
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
            field_issues = issue_codes.get(
                (follow_up.task_id, follow_up.target_field), set()
            )
            mentioned_not_obtained = (
                CheckerIssueCode.MENTIONED_NOT_OBTAINED in field_issues
            )
            pools = {
                ResolverAction.EXTRACT_KNOWN_SOURCE: follow_up.candidate_source_ids,
                ResolverAction.RETRY_RETRIEVAL: follow_up.retry_source_ids,
                ResolverAction.REEXTRACT_EXISTING: (
                    []
                    if mentioned_not_obtained
                    else follow_up.reextract_source_ids
                ),
            }
            allowed_actions = [
                action for action, source_ids in pools.items() if source_ids
            ]
            allowed_actions.append(ResolverAction.SEARCH_NEW_SOURCE)
            allowed_actions.append(ResolverAction.HUMAN_REVIEW)

            preferred_actions: list[ResolverAction] = []
            if follow_up.candidate_source_ids:
                preferred_actions.append(ResolverAction.EXTRACT_KNOWN_SOURCE)
            if mentioned_not_obtained and follow_up.retry_source_ids:
                preferred_actions.append(ResolverAction.RETRY_RETRIEVAL)
            if not mentioned_not_obtained and follow_up.reextract_source_ids:
                preferred_actions.append(ResolverAction.REEXTRACT_EXISTING)
            if follow_up.retry_source_ids:
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
                    *follow_up.candidate_source_ids,
                    *follow_up.retry_source_ids,
                    *follow_up.reextract_source_ids,
                ]
            )
            fallback_source_ids = [
                source_id
                for source_id in all_source_ids
                if source_id not in selected_sources
            ]
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
                        "Deterministic routing selected the least expensive "
                        "eligible action, preferring known sources over retry "
                        "or a new search."
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
        max_source_actions: int,
        max_search_tasks: int,
        max_queries_per_item: int,
    ) -> list[ResolverWorkItem]:
        expected_ids = [item.follow_up_id for item in deterministic_items]
        if set(item.follow_up_id for item in draft.items) != set(expected_ids):
            raise ValueError("Resolver draft must cover every selected follow-up once.")
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
                raise ValueError("Resolver draft selected a locally forbidden action.")
            source_field = source_pool_by_action.get(item.selected_action)
            allowed_source_ids = (
                list(getattr(follow_up, source_field)) if source_field else []
            )
            if source_field:
                if (
                    not item.selected_source_ids
                    or not set(item.selected_source_ids).issubset(allowed_source_ids)
                ):
                    raise ValueError("Resolver draft source selection is invalid.")
            elif item.selected_source_ids:
                raise ValueError("Resolver non-source action selected source IDs.")
            for source_id in item.selected_source_ids:
                if source_id not in execution_sources:
                    execution_sources.append(source_id)
            if len(execution_sources) > max_source_actions:
                raise ValueError("Resolver draft exceeds the source-action budget.")
            if item.selected_action == ResolverAction.SEARCH_NEW_SOURCE:
                if baseline.task_id not in search_tasks:
                    search_tasks.append(baseline.task_id)
                if len(search_tasks) > max_search_tasks:
                    raise ValueError("Resolver draft exceeds the search-task budget.")
            queries = _deduplicate(
                [*item.derived_queries, *baseline.queries]
            )[:max_queries_per_item]
            fallback_source_ids = [
                source_id
                for source_id in _deduplicate(
                    [
                        *follow_up.candidate_source_ids,
                        *follow_up.retry_source_ids,
                        *follow_up.reextract_source_ids,
                    ]
                )
                if source_id not in item.selected_source_ids
            ]
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
        if (
            checker_results.passed
            or checker_results.recommended_next_action
            != CheckerNextAction.RESOLVE_GAPS
            or not checker_results.follow_up_tasks
        ):
            raise ResolverValidationError(
                "Checker artifact does not contain unresolved work for Resolver."
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
