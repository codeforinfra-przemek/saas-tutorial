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

from .agents.checker import CheckerAgent, CheckerValidationError
from .agents.extractor import ExtractorAgent, ExtractorValidationError
from .agents.executor import (
    ExecutorAgent,
    ExecutorProviderError,
    ExecutorValidationError,
)
from .agents.planner import PlannerAgent, PlannerValidationError
from .agents.resolver import ResolverAgent, ResolverValidationError
from .agents.searcher import SearcherAgent, SearcherValidationError
from .catalog import CatalogError, load_question_catalog, select_questions
from .config import ConfigurationError, OpenAISettings
from .documents import DocumentFetcher, FetchPolicy
from .llm.openai_client import OpenAIPlannerClient, PlannerProviderError
from .llm.openai_checker_client import OpenAICheckerClient
from .llm.openai_extractor_client import OpenAIExtractorClient
from .llm.openai_resolver_client import OpenAIResolverClient
from .llm.openai_searcher_client import (
    OpenAISearcherClient,
    SearcherProviderError,
)
from .llm.protocol import (
    CheckerProviderError,
    ExtractorProviderError,
    ResolverProviderError,
)
from .schemas import (
    AgentFailureArtifact,
    AgentIterationUsage,
    CheckerAttemptFailure,
    ExecutorMode,
    ExtractionAttemptFailure,
    PlannerInput,
    ResearchDepth,
    ToolUsage,
)
from .storage.json_store import (
    DEFAULT_RUNS_DIR,
    checker_results_filename_for,
    executor_results_filename_for,
    extraction_results_filename_for,
    load_extraction_results,
    load_checker_results,
    load_research_plan,
    resolver_results_filename_for,
    load_resolver_results,
    load_search_results,
    reserve_artifact,
    save_agent_failure,
    save_checker_results,
    save_extraction_results,
    save_executor_results,
    save_research_plan,
    save_resolver_results,
    save_search_results,
    search_results_filename_for,
)
from .storage.document_archive import (
    RawDocumentArchive,
    document_archive_directory_name,
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
        description=(
            "Auditable franchise research loop "
            "(Planner + Searcher + Extractor + Checker + Resolver + Executor)."
        ),
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

    extract_parser = subparsers.add_parser(
        "extract",
        help="Fetch Searcher sources and optionally extract grounded raw claims.",
    )
    extract_parser.add_argument(
        "--sources",
        type=Path,
        required=True,
        help="Exact sources.json or sources-rNNN.json to consume.",
    )
    extract_parser.add_argument(
        "--plan",
        type=Path,
        help="Exact plan artifact; defaults to Searcher's plan_reference.",
    )
    extract_parser.add_argument(
        "--free",
        "--offline",
        dest="offline",
        action="store_true",
        help=(
            "Fetch and parse documents locally without OpenAI semantic extraction."
        ),
    )
    extract_parser.add_argument(
        "--iteration",
        type=_positive_int,
        help="Logical Extractor iteration; defaults to the Searcher iteration.",
    )
    extract_parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Exact Searcher source ID to process; repeatable.",
    )
    extract_parser.add_argument(
        "--limit-sources",
        type=_positive_int,
        default=5,
        help="Maximum selected source documents; safe trial default: 5.",
    )
    extract_parser.add_argument(
        "--max-document-bytes",
        type=_positive_int,
        default=40 * 1024 * 1024,
        help="Hard per-document download cap in bytes (default: 40 MiB).",
    )
    extract_parser.add_argument(
        "--max-document-chars",
        type=_positive_int,
        default=250_000,
        help="Maximum stored selected text characters per document (default: 250000).",
    )
    extract_parser.add_argument(
        "--max-pdf-scan-chars",
        type=_positive_int,
        default=2_000_000,
        help="Maximum locally parsed PDF text before selection (default: 2000000).",
    )
    extract_parser.add_argument(
        "--max-passages-per-task",
        type=_positive_int,
        default=6,
        help="Maximum candidate passages per task and document (default: 6).",
    )
    extract_parser.add_argument(
        "--max-api-calls",
        type=_positive_int,
        default=5,
        help="Hard paid Extractor request cap (default: 5).",
    )
    extract_parser.add_argument(
        "--max-evidence-chars-per-call",
        type=_positive_int,
        default=100_000,
        help="Hard evidence-text cap per OpenAI request (default: 100000).",
    )
    extract_parser.add_argument(
        "--model", help="Override OPENAI_MODEL for this invocation."
    )
    extract_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Artifact directory; defaults to the input Searcher artifact directory.",
    )

    check_parser = subparsers.add_parser(
        "check",
        help="Audit one exact Extractor artifact and create quality decisions.",
    )
    check_parser.add_argument(
        "--extractions",
        type=Path,
        required=True,
        help="Exact extractions.json or extractions-rNNN.json to consume.",
    )
    check_parser.add_argument(
        "--plan",
        type=Path,
        help="Exact plan artifact; defaults to Extractor's plan_reference.",
    )
    check_parser.add_argument(
        "--sources",
        type=Path,
        help="Exact Searcher artifact; defaults to Extractor's search_reference.",
    )
    check_parser.add_argument(
        "--free",
        "--offline",
        dest="offline",
        action="store_true",
        help="Run deterministic structural and coverage checks without OpenAI.",
    )
    check_parser.add_argument(
        "--iteration",
        type=_positive_int,
        help="Logical Checker iteration; defaults to the Extractor iteration.",
    )
    check_parser.add_argument(
        "--max-claims",
        type=_positive_int,
        default=100,
        help="Maximum raw claims reviewed in this iteration (default: 100).",
    )
    check_parser.add_argument(
        "--max-evidence-chars",
        type=_positive_int,
        default=100_000,
        help="Maximum quoted evidence characters sent to OpenAI (default: 100000).",
    )
    check_parser.add_argument(
        "--model", help="Override OPENAI_MODEL for this Checker invocation."
    )

    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Turn one paid Checker artifact into a bounded repair plan.",
    )
    resolve_parser.add_argument(
        "--check",
        type=Path,
        required=True,
        help="Exact paid check.json or check-rNNN.json to consume.",
    )
    resolve_parser.add_argument(
        "--plan",
        type=Path,
        help="Exact plan artifact; defaults to Checker's plan_reference.",
    )
    resolve_parser.add_argument(
        "--sources",
        type=Path,
        help="Exact Searcher artifact; defaults to Checker's search_reference.",
    )
    resolve_parser.add_argument(
        "--extractions",
        type=Path,
        help="Exact Extractor artifact; defaults to Checker's extraction_reference.",
    )
    resolve_parser.add_argument(
        "--free",
        "--offline",
        dest="offline",
        action="store_true",
        help="Build the repair strategy deterministically without OpenAI.",
    )
    resolve_parser.add_argument(
        "--iteration",
        type=_positive_int,
        help="Logical Resolver iteration; defaults to the Checker iteration.",
    )
    resolve_parser.add_argument(
        "--max-follow-ups",
        type=_positive_int,
        default=30,
        help="Maximum Checker follow-ups planned in this run (default: 30).",
    )
    resolve_parser.add_argument(
        "--max-source-actions",
        type=_positive_int,
        default=10,
        help="Maximum unique known sources scheduled in this run (default: 10).",
    )
    resolve_parser.add_argument(
        "--max-search-tasks",
        type=_positive_int,
        default=5,
        help="Maximum tasks allowed to require a new search (default: 5).",
    )
    resolve_parser.add_argument(
        "--max-queries-per-item",
        type=_positive_int,
        default=3,
        help="Maximum retained queries per follow-up (default: 3).",
    )
    resolve_parser.add_argument(
        "--model", help="Override OPENAI_MODEL for this Resolver invocation."
    )
    resolve_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Artifact directory; defaults to the input Checker directory.",
    )

    execute_parser = subparsers.add_parser(
        "execute",
        help="Execute Resolver batches and materialize merged Searcher/Extractor state.",
    )
    execute_parser.add_argument(
        "--resolution",
        type=Path,
        required=True,
        help="Exact resolution.json or resolution-rNNN.json to execute.",
    )
    execute_parser.add_argument(
        "--plan",
        type=Path,
        help="Exact plan artifact; defaults to Resolver's plan_reference.",
    )
    execute_parser.add_argument(
        "--sources",
        type=Path,
        help="Exact predecessor Searcher artifact; defaults to Resolver lineage.",
    )
    execute_parser.add_argument(
        "--extractions",
        type=Path,
        help="Exact predecessor Extractor artifact; defaults to Resolver lineage.",
    )
    execute_parser.add_argument(
        "--check",
        type=Path,
        help="Exact predecessor Checker artifact; defaults to Resolver lineage.",
    )
    execute_parser.add_argument(
        "--free",
        "--offline",
        dest="offline",
        action="store_true",
        help=(
            "Execute local retrieval/parsing and prepare search queries without "
            "OpenAI web search or semantic extraction."
        ),
    )
    execute_parser.add_argument(
        "--iteration",
        type=_positive_int,
        help="Execution iteration; defaults to Resolver iteration plus one.",
    )
    execute_parser.add_argument(
        "--max-search-calls",
        type=_positive_int,
        default=10,
        help="Global paid Searcher tool-call cap (default: 10).",
    )
    execute_parser.add_argument(
        "--min-queries-per-task",
        type=_positive_int,
        default=1,
        help="Minimum exact Resolver queries per search task (default: 1).",
    )
    execute_parser.add_argument(
        "--max-retry-tasks",
        type=_nonnegative_int,
        default=0,
        help="Optional paid Searcher quality retries (default: 0).",
    )
    execute_parser.add_argument(
        "--retry-search-calls",
        type=_positive_int,
        default=1,
        help="Tool calls reserved per optional Searcher retry (default: 1).",
    )
    execute_parser.add_argument(
        "--max-document-bytes",
        type=_positive_int,
        default=40 * 1024 * 1024,
        help="Hard per-document download cap (default: 40 MiB).",
    )
    execute_parser.add_argument(
        "--max-document-chars",
        type=_positive_int,
        default=250_000,
        help="Maximum stored selected document text (default: 250000).",
    )
    execute_parser.add_argument(
        "--max-pdf-scan-chars",
        type=_positive_int,
        default=2_000_000,
        help="Maximum locally parsed PDF text before selection (default: 2000000).",
    )
    execute_parser.add_argument(
        "--max-passages-per-task",
        type=_positive_int,
        default=6,
        help="Maximum evidence passages per task/document (default: 6).",
    )
    execute_parser.add_argument(
        "--max-evidence-chars-per-call",
        type=_positive_int,
        default=100_000,
        help="Hard evidence cap per paid Extractor request (default: 100000).",
    )
    execute_parser.add_argument(
        "--max-extractor-api-calls",
        type=_positive_int,
        default=20,
        help="Hard paid Extractor request cap for this execution (default: 20).",
    )
    execute_parser.add_argument(
        "--model", help="Override OPENAI_MODEL for Searcher and Extractor children."
    )
    execute_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Artifact directory; defaults to the Resolver directory.",
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
        "scope_source_ids": usage.scope_source_ids,
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


def _save_extractor_failure_ledger(
    *,
    plan_run_id: str,
    reference_path: Path,
    output_dir: Path | None,
    iteration: int,
    requested_model: str,
    usages: list[AgentIterationUsage],
    failed_attempts: list[ExtractionAttemptFailure],
    fallback_error_code: str,
) -> tuple[list[Path], list[str]]:
    """Best-effort persistence of every paid Extractor call after fatal failure."""

    usage_by_call = {usage.call_index: usage for usage in usages}
    failure_by_call = {
        failure.call_index: failure for failure in failed_attempts
    }
    call_indices = sorted(set(usage_by_call) | set(failure_by_call))
    paths: list[Path] = []
    errors: list[str] = []
    for call_index in call_indices:
        usage = usage_by_call.get(call_index)
        attempt = failure_by_call.get(call_index)
        scope_task_ids = (
            usage.scope_task_ids
            if usage is not None
            else attempt.scope_task_ids
            if attempt is not None
            else []
        )
        scope_source_ids = (
            usage.scope_source_ids
            if usage is not None
            else [attempt.source_id]
            if attempt is not None
            else []
        )
        failure = AgentFailureArtifact(
            failure_id=str(uuid4()),
            plan_run_id=plan_run_id,
            created_at=datetime.now(timezone.utc),
            error_code=(
                attempt.error_code
                if attempt is not None
                else fallback_error_code
            ),
            agent="extractor",
            iteration=(
                usage.iteration
                if usage is not None
                else iteration
            ),
            call_index=call_index,
            scope_task_ids=scope_task_ids,
            scope_source_ids=scope_source_ids,
            provider=usage.provider if usage is not None else "openai",
            requested_model=(
                usage.requested_model if usage is not None else requested_model
            ),
            usage=usage,
            observed_tool_calls=(
                sum(tool.calls for tool in usage.tool_usage)
                if usage is not None
                else 0
            ),
            tool_usage=usage.tool_usage if usage is not None else [],
            token_usage_unknown=usage is None,
        )
        try:
            paths.append(
                save_agent_failure(
                    failure,
                    reference_path,
                    output_dir=output_dir,
                )
            )
        except Exception as exc:
            errors.append(f"call {call_index}: {type(exc).__name__}")
    return paths, errors


def _run_extract(args: argparse.Namespace) -> int:
    search_results, search_sha256 = load_search_results(args.sources)
    plan_path = args.plan or Path(search_results.plan_reference)
    plan, plan_sha256 = load_research_plan(plan_path)
    iteration = args.iteration or search_results.iteration
    result_directory = args.output_dir or args.sources.parent
    expected_path = result_directory / extraction_results_filename_for(
        iteration,
        free=args.offline,
    )

    with reserve_artifact(expected_path):
        llm = None
        if not args.offline:
            settings = OpenAISettings.from_env()
            if args.model:
                settings = replace(settings, model=args.model)
            llm = OpenAIExtractorClient(settings)

        cached_documents = []
        cache_source_ids: set[str] = set()
        cache_terminal_source_ids: set[str] = set()
        if not args.offline:
            free_filename = extraction_results_filename_for(iteration, free=True)
            cache_candidates = [result_directory / free_filename]
            for cache_path in cache_candidates:
                if not cache_path.exists():
                    continue
                free_results, _ = load_extraction_results(cache_path)
                if (
                    free_results.generated_by == "deterministic"
                    and free_results.search_sha256 == search_sha256
                    and free_results.limits.max_document_bytes
                    == args.max_document_bytes
                    and free_results.limits.max_document_chars
                    == args.max_document_chars
                    and free_results.limits.max_pdf_scan_chars
                    == args.max_pdf_scan_chars
                ):
                    cached_documents = free_results.documents
                    cache_source_ids = {
                        document.source_id
                        for document in cached_documents
                        if document.retrieval_status.value == "fetched"
                        and document.parse_status.value in {"parsed", "partial"}
                    }
                    cache_terminal_source_ids = {
                        document.source_id
                        for document in cached_documents
                        if document.retrieval_status.value == "not_found"
                        or (
                            document.retrieval_status.value == "not_accessible"
                            and document.error_code
                            in {"access_denied", "anti_bot_page"}
                        )
                        or document.parse_status.value == "unsupported"
                    }
                    break

        fetch_policy = FetchPolicy(
            max_html_bytes=min(args.max_document_bytes, 5 * 1024 * 1024),
            max_pdf_bytes=args.max_document_bytes,
            max_text_chars=args.max_pdf_scan_chars,
        )
        fetcher = DocumentFetcher(policy=fetch_policy)
        raw_document_archiver = RawDocumentArchive(
            result_directory
            / document_archive_directory_name(iteration, free=args.offline),
            reference_root=result_directory,
        )
        try:
            results = ExtractorAgent(
                fetcher,
                llm,
                raw_document_archiver=raw_document_archiver,
            ).create_extraction_results(
                plan,
                search_results,
                plan_sha256=plan_sha256,
                search_sha256=search_sha256,
                search_reference=str(args.sources.resolve()),
                plan_reference=str(plan_path.resolve()),
                iteration=iteration,
                requested_source_ids=args.source,
                source_limit=args.limit_sources,
                max_document_bytes=args.max_document_bytes,
                max_document_chars=args.max_document_chars,
                max_pdf_scan_chars=args.max_pdf_scan_chars,
                max_passages_per_task=args.max_passages_per_task,
                max_evidence_chars_per_call=(
                    args.max_evidence_chars_per_call
                ),
                max_api_calls=args.max_api_calls,
                cached_documents=cached_documents,
                cached_document_origin="the same-iteration free Extractor artifact",
            )
        except ExtractorProviderError as exc:
            if not exc.usages and not exc.failed_attempts:
                raise
            failure_paths, ledger_errors = _save_extractor_failure_ledger(
                plan_run_id=plan.run_id,
                reference_path=plan_path,
                output_dir=args.output_dir,
                iteration=iteration,
                requested_model=(
                    llm.model_name if llm is not None else args.model or "unknown"
                ),
                usages=exc.usages,
                failed_attempts=exc.failed_attempts,
                fallback_error_code=exc.code,
            )
            ledger_note = (
                f" Failure ledger: {', '.join(str(path) for path in failure_paths)}."
                if failure_paths
                else ""
            )
            if ledger_errors:
                ledger_note += (
                    " Some failure-ledger writes also failed: "
                    f"{', '.join(ledger_errors)}."
                )
            raise ExtractorProviderError(
                f"{exc}{ledger_note}",
                code=exc.code,
                usages=exc.usages,
                failed_attempts=exc.failed_attempts,
            ) from None

        try:
            results_path = save_extraction_results(
                results,
                args.sources,
                output_dir=args.output_dir,
            )
        except Exception as exc:
            if results.generated_by != "openai" or (
                not results.agent_usage and not results.failed_attempts
            ):
                raise
            failure_paths, ledger_errors = _save_extractor_failure_ledger(
                plan_run_id=plan.run_id,
                reference_path=plan_path,
                output_dir=args.output_dir,
                iteration=results.iteration,
                requested_model=results.model or args.model or "unknown",
                usages=results.agent_usage,
                failed_attempts=results.failed_attempts,
                fallback_error_code="artifact_write_failed",
            )
            ledger_note = (
                f" Failure ledger: {', '.join(str(path) for path in failure_paths)}."
                if failure_paths
                else ""
            )
            if ledger_errors:
                ledger_note += (
                    " Some failure-ledger writes also failed: "
                    f"{', '.join(ledger_errors)}."
                )
            raise ExtractorProviderError(
                "Paid Extractor completed provider calls but its final artifact "
                f"could not be saved ({type(exc).__name__}).{ledger_note}",
                code="artifact_write_failed",
                usages=results.agent_usage,
                failed_attempts=results.failed_attempts,
            ) from None

    retrieval_statuses: dict[str, int] = {}
    parse_statuses: dict[str, int] = {}
    for document in results.documents:
        retrieval = document.retrieval_status.value
        parsed = document.parse_status.value
        retrieval_statuses[retrieval] = retrieval_statuses.get(retrieval, 0) + 1
        parse_statuses[parsed] = parse_statuses.get(parsed, 0) + 1
    task_statuses: dict[str, int] = {}
    for task_result in results.task_results:
        status = task_result.status.value
        task_statuses[status] = task_statuses.get(status, 0) + 1
    failed_call_indices = [
        attempt.call_index for attempt in results.failed_attempts
    ]
    selected_cache_ids = set(results.selected_source_ids) & cache_source_ids
    selected_terminal_cache_ids = (
        set(results.selected_source_ids) & cache_terminal_source_ids
    )
    summary = {
        "extraction_id": results.extraction_id,
        "plan_run_id": results.plan_run_id,
        "search_id": results.search_id,
        "search_sha256": results.search_sha256,
        "brand": results.brand_name,
        "generated_by": results.generated_by,
        "model": results.model,
        "iteration": results.iteration,
        "selected_sources": len(results.selected_source_ids),
        "unselected_sources": len(results.unselected_source_ids),
        "selected_tasks": len(results.selected_task_ids),
        "network_executed": results.network_executed,
        "provider_executed": results.provider_executed,
        "reused_free_documents": len(selected_cache_ids),
        "reused_free_terminal_results": len(selected_terminal_cache_ids),
        "document_retrieval_statuses": retrieval_statuses,
        "document_parse_statuses": parse_statuses,
        "evidence_passages": len(results.evidence_passages),
        "citations": len(results.citations),
        "raw_claims": len(results.claims),
        "task_statuses": task_statuses,
        "failed_attempts": [
            attempt.model_dump(mode="json")
            for attempt in results.failed_attempts
        ],
        "warnings": results.warnings,
        "agent_usage": [
            _usage_summary(usage) for usage in results.agent_usage
        ],
        "usage_totals": _usage_totals(
            results.agent_usage,
            failed_call_indices=failed_call_indices,
            has_unknown_token_usage=any(
                attempt.token_usage_unknown
                for attempt in results.failed_attempts
            ),
        ),
        "extractions_path": str(results_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _save_checker_failure_ledger(
    *,
    plan_run_id: str,
    reference_path: Path,
    iteration: int,
    requested_model: str,
    usages: list[AgentIterationUsage],
    failed_attempts: list[CheckerAttemptFailure],
    fallback_error_code: str,
    fallback_scope_task_ids: list[str],
    fallback_scope_source_ids: list[str],
) -> tuple[list[Path], list[str]]:
    """Best-effort persistence of the single bounded paid Checker call."""

    usage_by_call = {usage.call_index: usage for usage in usages}
    failure_by_call = {
        failure.call_index: failure for failure in failed_attempts
    }
    call_indices = sorted(set(usage_by_call) | set(failure_by_call)) or [1]
    paths: list[Path] = []
    errors: list[str] = []
    for call_index in call_indices:
        usage = usage_by_call.get(call_index)
        attempt = failure_by_call.get(call_index)
        scope_task_ids = (
            usage.scope_task_ids
            if usage is not None
            else attempt.scope_task_ids
            if attempt is not None
            else fallback_scope_task_ids
        )
        scope_source_ids = (
            usage.scope_source_ids
            if usage is not None
            else attempt.scope_source_ids
            if attempt is not None
            else fallback_scope_source_ids
        )
        failure = AgentFailureArtifact(
            failure_id=str(uuid4()),
            plan_run_id=plan_run_id,
            created_at=datetime.now(timezone.utc),
            error_code=(
                attempt.error_code
                if attempt is not None
                else fallback_error_code
            ),
            agent="checker",
            iteration=usage.iteration if usage is not None else iteration,
            call_index=call_index,
            scope_task_ids=scope_task_ids,
            scope_source_ids=scope_source_ids,
            provider=usage.provider if usage is not None else "openai",
            requested_model=(
                usage.requested_model if usage is not None else requested_model
            ),
            usage=usage,
            observed_tool_calls=(
                sum(tool.calls for tool in usage.tool_usage)
                if usage is not None
                else 0
            ),
            tool_usage=usage.tool_usage if usage is not None else [],
            token_usage_unknown=usage is None,
        )
        try:
            paths.append(save_agent_failure(failure, reference_path))
        except Exception as exc:
            errors.append(f"call {call_index}: {type(exc).__name__}")
    return paths, errors


def _run_check(args: argparse.Namespace) -> int:
    extraction_results, extraction_sha256 = load_extraction_results(
        args.extractions
    )
    search_path = args.sources or Path(extraction_results.search_reference)
    search_results, search_sha256 = load_search_results(search_path)
    plan_path = args.plan or Path(extraction_results.plan_reference)
    plan, plan_sha256 = load_research_plan(plan_path)
    iteration = args.iteration or extraction_results.iteration
    expected_path = args.extractions.parent / checker_results_filename_for(
        iteration,
        free=args.offline,
    )

    with reserve_artifact(expected_path):
        llm = None
        if not args.offline:
            settings = OpenAISettings.from_env()
            if args.model:
                settings = replace(settings, model=args.model)
            llm = OpenAICheckerClient(settings)

        try:
            results = CheckerAgent(llm).create_check_results(
                plan,
                search_results,
                extraction_results,
                plan_sha256=plan_sha256,
                search_sha256=search_sha256,
                extraction_sha256=extraction_sha256,
                extraction_reference=str(args.extractions.resolve()),
                plan_reference=str(plan_path.resolve()),
                search_reference=str(search_path.resolve()),
                iteration=iteration,
                max_claims=args.max_claims,
                max_evidence_chars=args.max_evidence_chars,
            )
        except CheckerProviderError as exc:
            requested_model = (
                llm.model_name if llm is not None else exc.requested_model
            )
            if requested_model is None:
                raise
            failure_paths, ledger_errors = _save_checker_failure_ledger(
                plan_run_id=plan.run_id,
                reference_path=args.extractions,
                iteration=exc.iteration or iteration,
                requested_model=requested_model,
                usages=exc.usages,
                failed_attempts=exc.failed_attempts,
                fallback_error_code=exc.code,
                fallback_scope_task_ids=(
                    exc.scope_task_ids or extraction_results.selected_task_ids
                ),
                fallback_scope_source_ids=(
                    exc.scope_source_ids or extraction_results.selected_source_ids
                ),
            )
            ledger_note = (
                f" Failure ledger: {', '.join(str(path) for path in failure_paths)}."
                if failure_paths
                else ""
            )
            if ledger_errors:
                ledger_note += (
                    " Some failure-ledger writes also failed: "
                    f"{', '.join(ledger_errors)}."
                )
            raise CheckerProviderError(
                f"{exc}{ledger_note}",
                code=exc.code,
                usages=exc.usages,
                agent=exc.agent,
                iteration=exc.iteration,
                call_index=exc.call_index,
                scope_task_ids=exc.scope_task_ids,
                scope_source_ids=exc.scope_source_ids,
                requested_model=exc.requested_model,
                failed_attempts=exc.failed_attempts,
            ) from None

        try:
            results_path = save_checker_results(results, args.extractions)
        except Exception as exc:
            if results.generated_by != "openai" or (
                not results.agent_usage and not results.failed_attempts
            ):
                raise
            failure_paths, ledger_errors = _save_checker_failure_ledger(
                plan_run_id=plan.run_id,
                reference_path=args.extractions,
                iteration=results.iteration,
                requested_model=results.model or "unknown",
                usages=results.agent_usage,
                failed_attempts=results.failed_attempts,
                fallback_error_code="artifact_write_failed",
                fallback_scope_task_ids=results.selected_task_ids,
                fallback_scope_source_ids=results.selected_source_ids,
            )
            ledger_note = (
                f" Failure ledger: {', '.join(str(path) for path in failure_paths)}."
                if failure_paths
                else ""
            )
            if ledger_errors:
                ledger_note += (
                    " Some failure-ledger writes also failed: "
                    f"{', '.join(ledger_errors)}."
                )
            raise CheckerProviderError(
                "Paid Checker completed its provider call but its final artifact "
                f"could not be saved ({type(exc).__name__}).{ledger_note}",
                code="artifact_write_failed",
                usages=results.agent_usage,
                iteration=results.iteration,
                scope_task_ids=results.selected_task_ids,
                scope_source_ids=results.selected_source_ids,
                requested_model=results.model,
                failed_attempts=results.failed_attempts,
            ) from None

    claim_verdicts: dict[str, int] = {}
    for decision in results.claim_decisions:
        verdict = decision.verdict.value
        claim_verdicts[verdict] = claim_verdicts.get(verdict, 0) + 1
    field_statuses: dict[str, int] = {}
    task_statuses: dict[str, int] = {}
    for task_result in results.task_results:
        status = task_result.status.value
        task_statuses[status] = task_statuses.get(status, 0) + 1
        for field_result in task_result.field_results:
            field_status = field_result.status.value
            field_statuses[field_status] = (
                field_statuses.get(field_status, 0) + 1
            )
    unsafe_severities: dict[str, int] = {}
    for unsafe_item in results.unsafe_items:
        severity = unsafe_item.severity.value
        unsafe_severities[severity] = unsafe_severities.get(severity, 0) + 1
    summary = {
        "check_id": results.check_id,
        "plan_run_id": results.plan_run_id,
        "search_id": results.search_id,
        "extraction_id": results.extraction_id,
        "plan_sha256": results.plan_sha256,
        "search_sha256": results.search_sha256,
        "extraction_sha256": results.extraction_sha256,
        "brand": results.brand_name,
        "generated_by": results.generated_by,
        "model": results.model,
        "iteration": results.iteration,
        "provider_executed": results.provider_executed,
        "selected_tasks": len(results.selected_task_ids),
        "selected_sources": len(results.selected_source_ids),
        "selected_claims": len(results.selected_claim_ids),
        "unevaluated_tasks": len(results.unevaluated_task_ids),
        "unevaluated_sources": len(results.unevaluated_source_ids),
        "scope_complete": results.scope_complete,
        "selected_scope_ready": results.selected_scope_ready,
        "claim_verdicts": claim_verdicts,
        "field_statuses": field_statuses,
        "task_statuses": task_statuses,
        "contradictions": len(results.contradictions),
        "unsafe_items": len(results.unsafe_items),
        "unsafe_severities": unsafe_severities,
        "critical_missing_fields_count": len(results.critical_missing_fields),
        "critical_missing_fields": results.critical_missing_fields,
        "unevaluated_critical_fields_count": len(
            results.unevaluated_critical_fields
        ),
        "follow_up_tasks": len(results.follow_up_tasks),
        "score_breakdown": results.score_breakdown.model_dump(mode="json"),
        "quality_score": results.quality_score,
        "quality_threshold": results.quality_threshold,
        "passed": results.passed,
        "recommended_next_action": results.recommended_next_action.value,
        "failed_attempts": [
            attempt.model_dump(mode="json")
            for attempt in results.failed_attempts
        ],
        "warnings": results.warnings,
        "agent_usage": [
            _usage_summary(usage) for usage in results.agent_usage
        ],
        "usage_totals": _usage_totals(
            results.agent_usage,
            failed_call_indices=[
                attempt.call_index for attempt in results.failed_attempts
            ],
            has_unknown_token_usage=any(
                attempt.token_usage_unknown
                for attempt in results.failed_attempts
            ),
        ),
        "check_path": str(results_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _save_resolver_failure_ledger(results, reference_path: Path) -> Path:
    """Preserve the single Resolver attempt if its final artifact cannot publish."""

    usage = results.agent_usage[0] if results.agent_usage else None
    attempt = results.failed_attempts[0] if results.failed_attempts else None
    failure = AgentFailureArtifact(
        failure_id=str(uuid4()),
        plan_run_id=results.plan_run_id,
        created_at=datetime.now(timezone.utc),
        error_code=(
            attempt.error_code if attempt is not None else "artifact_write_failed"
        ),
        agent="resolver",
        iteration=results.iteration,
        call_index=1,
        scope_task_ids=(
            usage.scope_task_ids
            if usage is not None
            else attempt.scope_task_ids
            if attempt is not None
            else []
        ),
        scope_source_ids=(
            usage.scope_source_ids
            if usage is not None
            else attempt.scope_source_ids
            if attempt is not None
            else []
        ),
        provider=usage.provider if usage is not None else "openai",
        requested_model=results.model,
        usage=usage,
        observed_tool_calls=0,
        tool_usage=[],
        token_usage_unknown=usage is None,
    )
    return save_agent_failure(failure, reference_path)


def _save_resolver_provider_failure(
    exc: ResolverProviderError,
    *,
    plan_run_id: str,
    reference_path: Path,
    iteration: int,
    requested_model: str,
) -> Path:
    """Persist Resolver usage when failure happens before a final result exists."""

    usage = exc.usage
    failure = AgentFailureArtifact(
        failure_id=str(uuid4()),
        plan_run_id=plan_run_id,
        created_at=datetime.now(timezone.utc),
        error_code=exc.code,
        agent="resolver",
        iteration=exc.iteration or iteration,
        call_index=exc.call_index or 1,
        scope_task_ids=(
            usage.scope_task_ids if usage is not None else exc.scope_task_ids
        ),
        scope_source_ids=(
            usage.scope_source_ids if usage is not None else exc.scope_source_ids
        ),
        provider=usage.provider if usage is not None else "openai",
        requested_model=requested_model,
        usage=usage,
        observed_tool_calls=0,
        tool_usage=[],
        token_usage_unknown=usage is None,
    )
    return save_agent_failure(failure, reference_path)


def _run_resolve(args: argparse.Namespace) -> int:
    checker_results, check_sha256 = load_checker_results(args.check)
    extraction_path = args.extractions or Path(
        checker_results.extraction_reference
    )
    extraction_results, extraction_sha256 = load_extraction_results(
        extraction_path
    )
    search_path = args.sources or Path(checker_results.search_reference)
    search_results, search_sha256 = load_search_results(search_path)
    plan_path = args.plan or Path(checker_results.plan_reference)
    plan, plan_sha256 = load_research_plan(plan_path)
    iteration = args.iteration or checker_results.iteration
    result_directory = args.output_dir or args.check.parent
    expected_path = result_directory / resolver_results_filename_for(
        iteration,
        free=args.offline,
    )

    with reserve_artifact(expected_path):
        llm = None
        if not args.offline:
            settings = OpenAISettings.from_env()
            if args.model:
                settings = replace(settings, model=args.model)
            llm = OpenAIResolverClient(settings)
        try:
            results = ResolverAgent(llm).create_resolution_results(
                plan,
                search_results,
                extraction_results,
                checker_results,
                plan_sha256=plan_sha256,
                search_sha256=search_sha256,
                extraction_sha256=extraction_sha256,
                check_sha256=check_sha256,
                check_reference=str(args.check.resolve()),
                plan_reference=str(plan_path.resolve()),
                search_reference=str(search_path.resolve()),
                extraction_reference=str(extraction_path.resolve()),
                iteration=iteration,
                max_follow_ups=args.max_follow_ups,
                max_source_actions=args.max_source_actions,
                max_search_tasks=args.max_search_tasks,
                max_queries_per_item=args.max_queries_per_item,
            )
        except ResolverProviderError as exc:
            requested_model = (
                llm.model_name if llm is not None else exc.requested_model
            )
            if requested_model is None:
                raise
            ledger_note = ""
            try:
                failure_path = _save_resolver_provider_failure(
                    exc,
                    plan_run_id=plan.run_id,
                    reference_path=args.check,
                    iteration=iteration,
                    requested_model=requested_model,
                )
            except Exception as ledger_exc:
                ledger_note = (
                    " Resolver failure ledger also failed with "
                    f"{type(ledger_exc).__name__}."
                )
            else:
                ledger_note = f" Provider usage saved to: {failure_path}."
            raise ResolverProviderError(
                f"{exc}{ledger_note}",
                code=exc.code,
                usage=exc.usage,
                iteration=exc.iteration or iteration,
                call_index=exc.call_index or 1,
                scope_task_ids=exc.scope_task_ids,
                scope_source_ids=exc.scope_source_ids,
                requested_model=requested_model,
                failed_attempts=exc.failed_attempts,
            ) from None
        try:
            results_path = save_resolver_results(
                results,
                args.check,
                output_dir=args.output_dir,
            )
        except Exception as exc:
            if results.generated_by != "openai":
                raise
            ledger_note = ""
            try:
                failure_path = _save_resolver_failure_ledger(results, args.check)
            except Exception as ledger_exc:
                ledger_note = (
                    " Resolver failure ledger also failed with "
                    f"{type(ledger_exc).__name__}."
                )
            else:
                ledger_note = f" Failure ledger: {failure_path}."
            raise ResolverProviderError(
                "Paid Resolver completed its provider attempt but its final "
                f"artifact could not be saved ({type(exc).__name__}).{ledger_note}",
                code="artifact_write_failed",
                usage=(results.agent_usage[0] if results.agent_usage else None),
                iteration=results.iteration,
                scope_task_ids=list(
                    dict.fromkeys(item.task_id for item in results.work_items)
                ),
                scope_source_ids=results.available_source_ids,
                requested_model=results.model,
                failed_attempts=results.failed_attempts,
            ) from None

    action_counts: dict[str, int] = {}
    for item in results.work_items:
        action = item.selected_action.value
        action_counts[action] = action_counts.get(action, 0) + 1
    summary = {
        "resolution_id": results.resolution_id,
        "check_id": results.check_id,
        "check_sha256": results.check_sha256,
        "brand": results.brand_name,
        "generated_by": results.generated_by,
        "strategy_source": results.strategy_source.value,
        "model": results.model,
        "iteration": results.iteration,
        "provider_executed": results.provider_executed,
        "selected_follow_ups": len(results.selected_follow_up_ids),
        "deferred_follow_ups": len(results.deferred_follow_up_ids),
        "work_item_actions": action_counts,
        "execution_batches": len(results.execution_batches),
        "execution_source_ids": results.execution_source_ids,
        "search_task_ids": results.search_task_ids,
        "ready_for_execution": results.ready_for_execution,
        "recommended_next_action": results.recommended_next_action.value,
        "failed_attempts": [
            attempt.model_dump(mode="json")
            for attempt in results.failed_attempts
        ],
        "warnings": results.warnings,
        "agent_usage": [
            _usage_summary(usage) for usage in results.agent_usage
        ],
        "usage_totals": _usage_totals(
            results.agent_usage,
            failed_call_indices=[
                attempt.call_index for attempt in results.failed_attempts
            ],
            has_unknown_token_usage=any(
                attempt.token_usage_unknown
                for attempt in results.failed_attempts
            ),
        ),
        "resolution_path": str(results_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _save_executor_failure_usages(
    *,
    plan_run_id: str,
    reference_path: Path,
    output_dir: Path,
    error_code: str,
    iteration: int,
    requested_model: str,
    usages: list[AgentIterationUsage],
    extraction_failed_attempts: list[ExtractionAttemptFailure] | None = None,
) -> list[Path]:
    """Persist all known child usage when a complete execution cannot publish."""

    failure_by_key = {
        ("extractor", attempt.call_index): attempt
        for attempt in (extraction_failed_attempts or [])
    }
    paths: list[Path] = []
    usage_keys: set[tuple[str, int]] = set()
    for usage in usages:
        key = (usage.agent, usage.call_index)
        usage_keys.add(key)
        attempt = failure_by_key.get(key)
        failure = AgentFailureArtifact(
            failure_id=str(uuid4()),
            plan_run_id=plan_run_id,
            created_at=datetime.now(timezone.utc),
            error_code=attempt.error_code if attempt is not None else error_code,
            agent=usage.agent,
            iteration=usage.iteration,
            call_index=usage.call_index,
            scope_task_ids=usage.scope_task_ids,
            scope_source_ids=usage.scope_source_ids,
            provider=usage.provider,
            requested_model=usage.requested_model,
            usage=usage,
            observed_tool_calls=sum(tool.calls for tool in usage.tool_usage),
            tool_usage=usage.tool_usage,
            token_usage_unknown=False,
        )
        paths.append(
            save_agent_failure(
                failure,
                reference_path,
                output_dir=output_dir,
            )
        )
    for attempt in extraction_failed_attempts or []:
        key = ("extractor", attempt.call_index)
        if key in usage_keys:
            continue
        failure = AgentFailureArtifact(
            failure_id=str(uuid4()),
            plan_run_id=plan_run_id,
            created_at=datetime.now(timezone.utc),
            error_code=attempt.error_code,
            agent="extractor",
            iteration=iteration,
            call_index=attempt.call_index,
            scope_task_ids=attempt.scope_task_ids,
            scope_source_ids=[attempt.source_id],
            requested_model=requested_model,
            usage=None,
            observed_tool_calls=0,
            tool_usage=[],
            token_usage_unknown=True,
        )
        paths.append(
            save_agent_failure(
                failure,
                reference_path,
                output_dir=output_dir,
            )
        )
    return paths


def _run_execute(args: argparse.Namespace) -> int:
    resolution, resolution_sha256 = load_resolver_results(args.resolution)
    plan_path = args.plan or Path(resolution.plan_reference)
    prior_search_path = args.sources or Path(resolution.search_reference)
    prior_extraction_path = args.extractions or Path(
        resolution.extraction_reference
    )
    check_path = args.check or Path(resolution.check_reference)
    plan, plan_sha256 = load_research_plan(plan_path)
    prior_search, prior_search_sha256 = load_search_results(prior_search_path)
    prior_extraction, prior_extraction_sha256 = load_extraction_results(
        prior_extraction_path
    )
    checker, check_sha256 = load_checker_results(check_path)
    iteration = args.iteration or (resolution.iteration + 1)
    result_directory = args.output_dir or args.resolution.parent
    if (
        any(document.content_path for document in prior_extraction.documents)
        and result_directory.resolve() != prior_extraction_path.parent.resolve()
    ):
        raise ExecutorValidationError(
            "Executor output directory must match the predecessor Extractor "
            "directory while inherited raw-document snapshots are referenced."
        )
    expected_search_path = result_directory / search_results_filename_for(
        iteration,
        offline=args.offline,
    )
    expected_extraction_path = (
        result_directory
        / extraction_results_filename_for(iteration, free=args.offline)
    )
    expected_execution_path = (
        result_directory
        / executor_results_filename_for(iteration, free=args.offline)
    )

    with (
        reserve_artifact(expected_search_path),
        reserve_artifact(expected_extraction_path),
        reserve_artifact(expected_execution_path),
    ):
        search_llm = None
        extraction_llm = None
        if not args.offline:
            settings = OpenAISettings.from_env()
            if args.model:
                settings = replace(settings, model=args.model)
            search_llm = OpenAISearcherClient(settings)
            extraction_llm = OpenAIExtractorClient(settings)

        fetch_policy = FetchPolicy(
            max_html_bytes=min(args.max_document_bytes, 5 * 1024 * 1024),
            max_pdf_bytes=args.max_document_bytes,
            max_text_chars=args.max_pdf_scan_chars,
        )
        fetcher = DocumentFetcher(policy=fetch_policy)
        raw_document_archiver = RawDocumentArchive(
            result_directory
            / document_archive_directory_name(
                iteration,
                free=args.offline,
            ),
            reference_root=result_directory,
        )
        agent = ExecutorAgent(
            SearcherAgent(search_llm),
            ExtractorAgent(
                fetcher,
                extraction_llm,
                raw_document_archiver=raw_document_archiver,
            ),
        )
        try:
            merged_search, merged_extraction, results = agent.execute(
                plan,
                prior_search,
                prior_extraction,
                checker,
                resolution,
                plan_sha256=plan_sha256,
                prior_search_sha256=prior_search_sha256,
                prior_extraction_sha256=prior_extraction_sha256,
                check_sha256=check_sha256,
                resolution_sha256=resolution_sha256,
                plan_reference=str(plan_path.resolve()),
                prior_search_reference=str(prior_search_path.resolve()),
                prior_extraction_reference=str(prior_extraction_path.resolve()),
                check_reference=str(check_path.resolve()),
                resolution_reference=str(args.resolution.resolve()),
                merged_search_reference=str(expected_search_path.resolve()),
                merged_extraction_reference=str(
                    expected_extraction_path.resolve()
                ),
                iteration=iteration,
                execution_mode=(
                    ExecutorMode.FREE if args.offline else ExecutorMode.PAID
                ),
                max_search_calls=args.max_search_calls,
                min_queries_per_task=args.min_queries_per_task,
                max_retry_tasks=args.max_retry_tasks,
                retry_search_calls=args.retry_search_calls,
                max_document_bytes=args.max_document_bytes,
                max_document_chars=args.max_document_chars,
                max_pdf_scan_chars=args.max_pdf_scan_chars,
                max_passages_per_task=args.max_passages_per_task,
                max_evidence_chars_per_call=(
                    args.max_evidence_chars_per_call
                ),
                max_extractor_api_calls=args.max_extractor_api_calls,
            )
        except ExecutorProviderError as exc:
            paths = _save_executor_failure_usages(
                plan_run_id=plan.run_id,
                reference_path=args.resolution,
                output_dir=result_directory,
                error_code=exc.code,
                iteration=iteration,
                requested_model=(
                    extraction_llm.model_name
                    if extraction_llm is not None
                    else args.model or "unknown"
                ),
                usages=exc.usages,
                extraction_failed_attempts=exc.extraction_failed_attempts,
            )
            raise ExecutorProviderError(
                f"{exc} Failure ledger: {', '.join(str(path) for path in paths)}.",
                code=exc.code,
                usages=exc.usages,
                extraction_failed_attempts=exc.extraction_failed_attempts,
            ) from None
        except SearcherProviderError as exc:
            paths = _save_executor_failure_usages(
                plan_run_id=plan.run_id,
                reference_path=args.resolution,
                output_dir=result_directory,
                error_code=exc.code,
                iteration=iteration,
                requested_model=(
                    search_llm.model_name
                    if search_llm is not None
                    else args.model or "unknown"
                ),
                usages=exc.usages,
            )
            if not paths and (
                exc.agent is not None
                and exc.call_index is not None
                and exc.requested_model is not None
            ):
                paths.append(
                    save_agent_failure(
                        AgentFailureArtifact(
                            failure_id=str(uuid4()),
                            plan_run_id=plan.run_id,
                            created_at=datetime.now(timezone.utc),
                            error_code=exc.code,
                            agent=exc.agent,
                            iteration=exc.iteration or iteration,
                            call_index=exc.call_index,
                            scope_task_ids=exc.scope_task_ids,
                            requested_model=exc.requested_model,
                            usage=None,
                            observed_tool_calls=exc.observed_tool_calls,
                            tool_usage=exc.tool_usage,
                            token_usage_unknown=True,
                        ),
                        args.resolution,
                        output_dir=result_directory,
                    )
                )
            ledger = f" Failure ledger: {', '.join(str(path) for path in paths)}."
            raise SearcherProviderError(
                f"{exc}{ledger}",
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

        try:
            merged_search_path = save_search_results(
                merged_search,
                plan_path,
                output_dir=result_directory,
            )
            merged_extraction_path = save_extraction_results(
                merged_extraction,
                merged_search_path,
                output_dir=result_directory,
            )
            execution_path = save_executor_results(
                results,
                args.resolution,
                output_dir=result_directory,
            )
        except Exception as exc:
            if not results.agent_usage:
                raise
            paths = _save_executor_failure_usages(
                plan_run_id=plan.run_id,
                reference_path=args.resolution,
                output_dir=result_directory,
                error_code="artifact_write_failed",
                iteration=iteration,
                requested_model=results.agent_usage[0].requested_model,
                usages=results.agent_usage,
                extraction_failed_attempts=merged_extraction.failed_attempts,
            )
            raise ExecutorProviderError(
                "Paid Executor child calls completed but final artifacts could "
                f"not all be saved ({type(exc).__name__}). Failure ledger: "
                f"{', '.join(str(path) for path in paths)}.",
                code="artifact_write_failed",
                usages=results.agent_usage,
                extraction_failed_attempts=merged_extraction.failed_attempts,
            ) from None

    batch_statuses: dict[str, int] = {}
    for batch in results.batch_results:
        status = batch.status.value
        batch_statuses[status] = batch_statuses.get(status, 0) + 1
    action_counts: dict[str, int] = {}
    for batch in results.batch_results:
        action = batch.action.value
        action_counts[action] = action_counts.get(action, 0) + 1
    child_failed_attempts = [
        *merged_search.failed_attempts,
        *merged_extraction.failed_attempts,
    ]
    execution_usage_totals = _usage_totals(
        results.agent_usage,
        failed_call_indices=[
            attempt.call_index for attempt in child_failed_attempts
        ],
        has_unknown_token_usage=any(
            attempt.token_usage_unknown for attempt in child_failed_attempts
        ),
    )
    execution_usage_totals["api_attempts_recorded"] = len(
        {
            *( (usage.agent, usage.call_index) for usage in results.agent_usage ),
            *(
                ("searcher", attempt.call_index)
                for attempt in merged_search.failed_attempts
            ),
            *(
                ("extractor", attempt.call_index)
                for attempt in merged_extraction.failed_attempts
            ),
        }
    )
    summary = {
        "execution_id": results.execution_id,
        "resolution_id": results.resolution_id,
        "resolution_sha256": results.resolution_sha256,
        "brand": results.brand_name,
        "execution_mode": results.execution_mode.value,
        "iteration": results.iteration,
        "batches": len(results.batch_results),
        "batch_actions": action_counts,
        "batch_statuses": batch_statuses,
        "search_executed": results.search_executed,
        "network_executed": results.network_executed,
        "provider_executed": results.provider_executed,
        "processed_sources": len(results.processed_source_ids),
        "retried_sources": len(results.retried_source_ids),
        "cached_sources": len(results.cached_source_ids),
        "preserved_processed_sources": len(
            results.preserved_processed_source_ids
        ),
        "new_sources": len(results.new_source_ids),
        "inherited_sources": len(results.inherited_source_ids),
        "pending_human_follow_ups": len(
            results.pending_human_follow_up_ids
        ),
        "merged_search_id": results.merged_search_id,
        "merged_search_sha256": results.merged_search_sha256,
        "merged_sources": len(merged_search.sources),
        "merged_extraction_id": results.merged_extraction_id,
        "merged_extraction_sha256": results.merged_extraction_sha256,
        "merged_documents": len(merged_extraction.documents),
        "merged_claims": len(merged_extraction.claims),
        "ready_for_checker": results.ready_for_checker,
        "recommended_next_action": results.recommended_next_action.value,
        "warnings": results.warnings,
        "agent_usage": [_usage_summary(usage) for usage in results.agent_usage],
        "usage_totals": execution_usage_totals,
        "sources_path": str(merged_search_path),
        "extractions_path": str(merged_extraction_path),
        "execution_path": str(execution_path),
        "next_command": (
            f".venv/bin/python -m datacollector check --extractions "
            f"{merged_extraction_path} --iteration {iteration}"
        ),
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
        if args.command == "extract":
            return _run_extract(args)
        if args.command == "check":
            return _run_check(args)
        if args.command == "resolve":
            return _run_resolve(args)
        if args.command == "execute":
            return _run_execute(args)
        if args.command == "questions":
            return _run_questions(args)
    except (
        CatalogError,
        ConfigurationError,
        PlannerProviderError,
        PlannerValidationError,
        CheckerProviderError,
        CheckerValidationError,
        ExtractorProviderError,
        ExtractorValidationError,
        ExecutorProviderError,
        ExecutorValidationError,
        SearcherProviderError,
        SearcherValidationError,
        ResolverProviderError,
        ResolverValidationError,
        ValidationError,
        OSError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"Unknown command: {args.command}")
    return 2
