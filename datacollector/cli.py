"""Command-line entry point for the standalone franchise data collector."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from .agents.planner import PlannerAgent, PlannerValidationError
from .agents.searcher import SearcherAgent, SearcherValidationError
from .catalog import CatalogError, load_question_catalog, select_questions
from .config import ConfigurationError, OpenAISettings
from .llm.openai_client import OpenAIPlannerClient, PlannerProviderError
from .llm.openai_searcher_client import (
    OpenAISearcherClient,
    SearcherProviderError,
)
from .schemas import (
    AgentFailureArtifact,
    AgentIterationUsage,
    PlannerInput,
    ResearchDepth,
    ToolUsage,
)
from .storage.json_store import (
    DEFAULT_RUNS_DIR,
    load_research_plan,
    reserve_artifact,
    save_agent_failure,
    save_research_plan,
    save_search_results,
    search_results_filename_for,
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m datacollector",
        description="Auditable franchise research loop (Planner + Searcher MVP).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser(
        "plan", help="Create and store a research plan for one franchise brand."
    )
    plan_parser.add_argument("--brand", required=True, help="Franchise brand name.")
    plan_parser.add_argument(
        "--country", default="PL", help="Two-letter target country code (default: PL)."
    )
    plan_parser.add_argument(
        "--region", action="append", default=[], help="Target region/state; repeatable."
    )
    plan_parser.add_argument(
        "--language",
        action="append",
        default=[],
        help="Research language; repeatable (default: pl, en).",
    )
    plan_parser.add_argument(
        "--depth",
        choices=[depth.value for depth in ResearchDepth],
        default=ResearchDepth.DUE_DILIGENCE.value,
    )
    plan_parser.add_argument("--known-legal-name")
    plan_parser.add_argument("--known-official-website")
    plan_parser.add_argument(
        "--existing-field",
        action="append",
        default=[],
        help="Existing structured field to verify rather than collect; repeatable.",
    )
    plan_parser.add_argument("--max-queries-per-task", type=int, default=3)
    plan_parser.add_argument("--quality-threshold", type=int, default=80)
    plan_parser.add_argument("--max-rounds", type=int, default=3)
    plan_parser.add_argument(
        "--iteration",
        type=_positive_int,
        default=1,
        help="Logical Planner iteration recorded in usage metadata (default: 1).",
    )
    plan_parser.add_argument(
        "--allow-personal-data",
        action="store_true",
        help="Include optional personal-data tasks; disabled by default.",
    )
    plan_parser.add_argument(
        "--free",
        "--offline",
        dest="offline",
        action="store_true",
        help="Use only deterministic catalog rules; make no OpenAI API call.",
    )
    plan_parser.add_argument(
        "--model", help="Override OPENAI_MODEL for this invocation."
    )
    plan_parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help="Root directory for run artifacts.",
    )

    questions_parser = subparsers.add_parser(
        "questions", help="Inspect selected canonical questions without an API call."
    )
    questions_parser.add_argument(
        "--country", default="PL", help="Two-letter target country code."
    )
    questions_parser.add_argument(
        "--depth",
        choices=[depth.value for depth in ResearchDepth],
        default=ResearchDepth.DUE_DILIGENCE.value,
    )
    questions_parser.add_argument("--allow-personal-data", action="store_true")

    search_parser = subparsers.add_parser(
        "search",
        help="Run Searcher against an explicit plan and store source candidates.",
    )
    search_parser.add_argument(
        "--plan",
        type=Path,
        required=True,
        help="Exact plan.json or plan-free.json to consume.",
    )
    search_parser.add_argument(
        "--free",
        "--offline",
        dest="offline",
        action="store_true",
        help="Prepare a deterministic query workload; perform no network search.",
    )
    search_parser.add_argument(
        "--iteration",
        type=_positive_int,
        default=1,
        help="Logical Searcher loop iteration (default: 1).",
    )
    search_parser.add_argument(
        "--task",
        action="append",
        default=[],
        help="Task ID or catalog question ID to search; repeatable.",
    )
    search_parser.add_argument(
        "--limit-tasks",
        type=_positive_int,
        default=5,
        help="Maximum selected tasks; safe trial default: 5.",
    )
    search_parser.add_argument(
        "--max-search-calls",
        type=_positive_int,
        default=10,
        help=(
            "Global hard tool-call cap across initial search and retries "
            "(default: 10)."
        ),
    )
    search_parser.add_argument(
        "--min-queries-per-task",
        type=_positive_int,
        default=1,
        help="Minimum exact Planner queries required per task (default: 1).",
    )
    search_parser.add_argument(
        "--max-retry-tasks",
        type=_nonnegative_int,
        default=0,
        help=(
            "Opt-in paid quality retries, one task per request; 0 disables "
            "additional spend (default: 0)."
        ),
    )
    search_parser.add_argument(
        "--retry-search-calls",
        type=_positive_int,
        default=1,
        help="Maximum tool calls reserved for each opt-in retry (default: 1).",
    )
    search_parser.add_argument(
        "--model", help="Override OPENAI_MODEL for this invocation."
    )
    search_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Artifact directory; defaults to the input plan directory.",
    )

    return parser


def _planner_input_from_args(args: argparse.Namespace) -> PlannerInput:
    return PlannerInput(
        brand_name=args.brand,
        target_country=args.country,
        target_regions=args.region,
        research_languages=args.language or ["pl", "en"],
        depth=args.depth,
        known_legal_name=args.known_legal_name,
        known_official_website=args.known_official_website,
        existing_fields=args.existing_field,
        max_queries_per_task=args.max_queries_per_task,
        quality_threshold=args.quality_threshold,
        max_rounds=args.max_rounds,
        allow_personal_data=args.allow_personal_data,
    )


def _run_plan(args: argparse.Namespace) -> int:
    catalog = load_question_catalog()
    planner_input = _planner_input_from_args(args)

    llm = None
    if not args.offline:
        settings = OpenAISettings.from_env()
        if args.model:
            settings = replace(settings, model=args.model)
        llm = OpenAIPlannerClient(settings)

    plan = PlannerAgent(catalog, llm).create_plan(
        planner_input,
        iteration=args.iteration,
    )
    plan_path = save_research_plan(plan, args.output_dir)
    summary = {
        "run_id": plan.run_id,
        "brand": plan.planner_input.brand_name,
        "country": plan.planner_input.target_country,
        "depth": plan.planner_input.depth.value,
        "generated_by": plan.generated_by,
        "model": plan.model,
        "tasks": len(plan.tasks),
        "critical_fields": len(plan.critical_fields),
        "agent_usage": [_usage_summary(usage) for usage in plan.agent_usage],
        "plan_path": str(plan_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _usage_summary(usage: AgentIterationUsage) -> dict[str, object]:
    tool_calls = sum(item.calls for item in usage.tool_usage)
    tool_cost = sum(
        (item.estimated_cost_usd for item in usage.tool_usage),
        start=Decimal("0"),
    )
    return {
        "agent": usage.agent,
        "iteration": usage.iteration,
        "call_index": usage.call_index,
        "scope_task_ids": usage.scope_task_ids,
        "input_tokens": usage.tokens.input_tokens,
        "cached_input_tokens": usage.tokens.cached_input_tokens,
        "cache_write_input_tokens": usage.tokens.cache_write_input_tokens,
        "output_tokens": usage.tokens.output_tokens,
        "reasoning_tokens": usage.tokens.reasoning_tokens,
        "total_tokens": usage.tokens.total_tokens,
        "tool_calls": tool_calls,
        "tool_cost_usd": str(tool_cost),
        "estimated_cost_usd": (
            str(usage.cost_estimate.total_estimated_cost_usd)
            if usage.cost_estimate
            else None
        ),
    }


def _usage_totals(
    usages: list[AgentIterationUsage],
    *,
    failed_call_indices: list[int] | None = None,
    unledgered_tool_usage: list[ToolUsage] | None = None,
    has_unknown_token_usage: bool = False,
) -> dict[str, object]:
    known_costs = [
        usage.cost_estimate.total_estimated_cost_usd
        for usage in usages
        if usage.cost_estimate is not None
    ]
    tool_usage = [
        *(item for usage in usages for item in usage.tool_usage),
        *(unledgered_tool_usage or []),
    ]
    recorded_call_indices = {
        *(usage.call_index for usage in usages),
        *(failed_call_indices or []),
    }
    return {
        "api_attempts_recorded": len(recorded_call_indices),
        "api_calls_with_usage": len(usages),
        "input_tokens": sum(usage.tokens.input_tokens for usage in usages),
        "cached_input_tokens": sum(
            usage.tokens.cached_input_tokens for usage in usages
        ),
        "cache_write_input_tokens": sum(
            usage.tokens.cache_write_input_tokens for usage in usages
        ),
        "output_tokens": sum(usage.tokens.output_tokens for usage in usages),
        "reasoning_tokens": sum(
            usage.tokens.reasoning_tokens for usage in usages
        ),
        "total_tokens": sum(usage.tokens.total_tokens for usage in usages),
        "tool_calls": sum(item.calls for item in tool_usage),
        "tool_cost_usd": str(
            sum(
                (item.estimated_cost_usd for item in tool_usage),
                start=Decimal("0"),
            )
        ),
        "estimated_cost_usd": (
            str(sum(known_costs, start=Decimal("0")))
            if len(known_costs) == len(usages) and not has_unknown_token_usage
            else None
        ),
    }


def _run_search(args: argparse.Namespace) -> int:
    plan, plan_sha256 = load_research_plan(args.plan)
    result_directory = args.output_dir or args.plan.parent
    expected_path = result_directory / search_results_filename_for(
        args.iteration,
        offline=args.offline,
    )
    with reserve_artifact(expected_path):
        llm = None
        if not args.offline:
            settings = OpenAISettings.from_env()
            if args.model:
                settings = replace(settings, model=args.model)
            llm = OpenAISearcherClient(settings)

        try:
            results = SearcherAgent(llm).create_search_results(
                plan,
                plan_sha256=plan_sha256,
                plan_reference=str(args.plan.resolve()),
                iteration=args.iteration,
                requested_task_ids=args.task,
                task_limit=args.limit_tasks,
                max_search_calls=args.max_search_calls,
                min_queries_per_task=args.min_queries_per_task,
                max_retry_tasks=args.max_retry_tasks,
                retry_search_calls=args.retry_search_calls,
            )
        except SearcherProviderError as exc:
            if not exc.usages and not exc.observed_tool_calls:
                raise
            failure_artifacts: list[AgentFailureArtifact] = []
            for usage in exc.usages:
                billed_tool_calls = sum(item.calls for item in usage.tool_usage)
                observed_tool_calls = max(
                    billed_tool_calls,
                    exc.observed_tool_calls if len(exc.usages) == 1 else 0,
                )
                failure_artifacts.append(
                    AgentFailureArtifact(
                        failure_id=str(uuid4()),
                        plan_run_id=plan.run_id,
                        created_at=datetime.now(timezone.utc),
                        error_code=exc.code,
                        agent=usage.agent,
                        iteration=usage.iteration,
                        call_index=usage.call_index,
                        scope_task_ids=usage.scope_task_ids,
                        provider=usage.provider,
                        requested_model=usage.requested_model,
                        usage=usage,
                        observed_tool_calls=observed_tool_calls,
                        tool_usage=usage.tool_usage,
                        token_usage_unknown=False,
                    )
                )
            if not exc.usages:
                if (
                    exc.agent is None
                    or exc.iteration is None
                    or exc.call_index is None
                    or exc.requested_model is None
                ):
                    raise
                failure_artifacts.append(
                    AgentFailureArtifact(
                        failure_id=str(uuid4()),
                        plan_run_id=plan.run_id,
                        created_at=datetime.now(timezone.utc),
                        error_code=exc.code,
                        agent=exc.agent,
                        iteration=exc.iteration,
                        call_index=exc.call_index,
                        scope_task_ids=exc.scope_task_ids,
                        requested_model=exc.requested_model,
                        usage=None,
                        observed_tool_calls=exc.observed_tool_calls,
                        tool_usage=exc.tool_usage,
                        token_usage_unknown=True,
                    )
                )
            failure_paths = [
                save_agent_failure(
                    failure,
                    args.plan,
                    output_dir=args.output_dir,
                )
                for failure in failure_artifacts
            ]
            raise SearcherProviderError(
                f"{exc} Provider usage saved to: "
                f"{', '.join(str(path) for path in failure_paths)}.",
                code=exc.code,
                usages=exc.usages,
                observed_tool_calls=exc.observed_tool_calls,
                tool_usage=exc.tool_usage,
                agent=exc.agent,
                iteration=exc.iteration,
                call_index=exc.call_index,
                scope_task_ids=exc.scope_task_ids,
                requested_model=exc.requested_model,
            ) from None

        results_path = save_search_results(
            results,
            args.plan,
            output_dir=args.output_dir,
        )
    status_counts: dict[str, int] = {}
    for result in results.task_results:
        status_counts[result.status.value] = (
            status_counts.get(result.status.value, 0) + 1
        )
    query_coverage_counts: dict[str, int] = {}
    for result in results.task_results:
        query_coverage_counts[result.query_coverage.value] = (
            query_coverage_counts.get(result.query_coverage.value, 0) + 1
        )
    action_candidate_urls = {
        url
        for action in results.actions
        for url in [*action.source_urls, action.target_url]
        if url is not None
    }
    summary = {
        "search_id": results.search_id,
        "plan_run_id": results.plan_run_id,
        "plan_sha256": results.plan_sha256,
        "brand": results.brand_name,
        "generated_by": results.generated_by,
        "model": results.model,
        "iteration": results.iteration,
        "selected_tasks": len(results.selected_task_ids),
        "unselected_tasks": len(results.unselected_task_ids),
        "search_actions": len(results.actions),
        "action_candidate_urls": len(action_candidate_urls),
        "sources": len(results.sources),
        "provider_observed_sources": sum(
            source.provider_observed for source in results.sources
        ),
        "provider_verified_sources": sum(
            source.provider_observed for source in results.sources
        ),
        "plan_seed_sources": sum(
            not source.provider_observed for source in results.sources
        ),
        "task_statuses": status_counts,
        "task_query_coverage": query_coverage_counts,
        "failed_attempts": [
            attempt.model_dump(mode="json") for attempt in results.failed_attempts
        ],
        "warnings": results.warnings,
        "agent_usage": [_usage_summary(usage) for usage in results.agent_usage],
        "usage_totals": _usage_totals(
            results.agent_usage,
            failed_call_indices=[
                attempt.call_index for attempt in results.failed_attempts
            ],
            unledgered_tool_usage=[
                tool
                for attempt in results.failed_attempts
                if not attempt.usage_recorded
                for tool in attempt.tool_usage
            ],
            has_unknown_token_usage=any(
                attempt.token_usage_unknown
                for attempt in results.failed_attempts
            ),
        ),
        "sources_path": str(results_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _run_questions(args: argparse.Namespace) -> int:
    catalog = load_question_catalog()
    planner_input = PlannerInput(
        brand_name="<brand>",
        target_country=args.country,
        depth=args.depth,
        allow_personal_data=args.allow_personal_data,
    )
    selected = select_questions(catalog, planner_input)
    output = {
        "catalog_version": catalog.version,
        "country": planner_input.target_country,
        "depth": planner_input.depth.value,
        "question_count": len(selected),
        "questions": [
            {
                "id": question.id,
                "section": section_id,
                "fdd_items": question.fdd_items,
                "requirement": question.requirement.value,
                "title": question.title,
                "question": question.question,
                "target_fields": question.target_fields,
            }
            for section_id, question in selected
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            return _run_plan(args)
        if args.command == "search":
            return _run_search(args)
        if args.command == "questions":
            return _run_questions(args)
    except (
        CatalogError,
        ConfigurationError,
        PlannerProviderError,
        PlannerValidationError,
        SearcherProviderError,
        SearcherValidationError,
        ValidationError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"Unknown command: {args.command}")
    return 2
