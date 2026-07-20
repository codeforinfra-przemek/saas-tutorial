"""Command-line entry point for the standalone franchise data collector."""

from __future__ import annotations

import argparse
import json
import re
import sys
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
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
from .agents.normalizer import NormalizerAgent, NormalizerValidationError
from .agents.planner import PlannerAgent, PlannerValidationError
from .agents.reviewer import (
    HumanReviewer,
    HumanReviewValidationError,
    render_review_html,
)
from .agents.resolver import ResolverAgent, ResolverValidationError
from .agents.searcher import SearcherAgent, SearcherValidationError
from .catalog import CatalogError, load_question_catalog, select_questions
from .config import ConfigurationError, OpenAISettings
from .documents import DocumentFetcher, FetchPolicy
from .llm.openai_client import OpenAIPlannerClient, PlannerProviderError
from .llm.openai_checker_client import OpenAICheckerClient
from .llm.openai_extractor_client import OpenAIExtractorClient
from .llm.openai_resolver_client import OpenAIResolverClient
from .llm.openai_normalizer_client import OpenAINormalizerClient
from .llm.openai_searcher_client import (
    OpenAISearcherClient,
    SearcherProviderError,
)
from .llm.protocol import (
    CheckerProviderError,
    ExtractorProviderError,
    NormalizerProviderError,
    ResolverProviderError,
)
from .loop import (
    LoopNextAction,
    LoopPolicy,
    LoopRoundResult,
    LoopRunResults,
    LoopStageUsage,
    LoopStopReason,
    LoopValidationError,
)
from .schemas import (
    AgentFailureArtifact,
    AgentIterationUsage,
    CheckerAttemptFailure,
    CheckerMode,
    CheckerNextAction,
    ExecutorMode,
    ExtractionAttemptFailure,
    HumanReviewDecision,
    NormalizerMode,
    PlannerInput,
    ResearchDepth,
    ToolUsage,
)
from .storage.json_store import (
    DEFAULT_RUNS_DIR,
    checker_results_filename_for,
    executor_results_filename_for,
    extraction_results_filename_for,
    reconciled_extraction_results_filename_for,
    load_extraction_results,
    load_executor_results,
    load_human_review_results,
    load_loop_results,
    load_checker_results,
    load_normalizer_results,
    load_research_plan,
    resolver_results_filename_for,
    normalizer_results_filename_for,
    human_review_paths_for,
    load_resolver_results,
    load_search_results,
    reserve_artifact,
    save_agent_failure,
    save_checker_results,
    save_extraction_results,
    save_normalizer_results,
    save_human_review_results,
    save_loop_results,
    save_reconciled_extraction_results,
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


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError("must be a decimal number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m datacollector",
        description=(
            "Auditable franchise research loop "
            "(Planner + Searcher + Extractor + Checker + Resolver + Executor + "
            "Loop Orchestrator + Normalizer + Human Review)."
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
        "--max-candidate-routes",
        type=_nonnegative_int,
        default=5,
        help="Maximum deterministic URL promotions from provider action traces (default: 5).",
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
        "--incremental",
        action="store_true",
        help=(
            "Review only changed Executor task scopes and inherit successful paid "
            "judgments through verified predecessor lineage."
        ),
    )
    check_parser.add_argument(
        "--iteration",
        type=_positive_int,
        help="Logical Checker iteration; defaults to the Extractor iteration.",
    )
    check_parser.add_argument(
        "--max-claims",
        type=_positive_int,
        default=500,
        help="Maximum raw claims reviewed in this iteration (default: 500).",
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
    resolver_exhausted_scope_policy = resolve_parser.add_mutually_exclusive_group()
    resolver_exhausted_scope_policy.add_argument(
        "--allow-round-limit",
        action="store_true",
        help=(
            "Explicitly override the plan max_rounds safety gate after inspecting "
            "the complete predecessor lineage."
        ),
    )
    resolver_exhausted_scope_policy.add_argument(
        "--advance-with-documented-gaps",
        action="store_true",
        help=(
            "After the Planner repair limit, preserve unresolved selected-scope "
            "gaps and schedule the next unevaluated research batch."
        ),
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
        "--max-candidate-routes",
        type=_nonnegative_int,
        default=5,
        help="Maximum deterministic Candidate Router promotions (default: 5).",
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

    loop_parser = subparsers.add_parser(
        "loop",
        help=(
            "Run bounded paid Checker → Resolver → Executor cycles and optionally "
            "normalize a passing result."
        ),
    )
    loop_parser.add_argument(
        "--check",
        type=Path,
        required=True,
        help="Exact paid Checker artifact from which to continue the loop.",
    )
    loop_parser.add_argument(
        "--max-rounds",
        type=_positive_int,
        default=3,
        help="Maximum additional repair or scope-expansion cycles (default: 3).",
    )
    loop_parser.add_argument(
        "--max-cost-usd",
        type=_positive_decimal,
        default=Decimal("1.00"),
        help=(
            "Maximum incremental estimated cost for this invocation (default: "
            "1.00); evaluated after each complete cycle."
        ),
    )
    loop_parser.add_argument(
        "--min-quality-improvement",
        type=_nonnegative_int,
        default=1,
        help="Minimum positive score increase counted as quality progress (default: 1).",
    )
    loop_parser.add_argument(
        "--max-stagnant-rounds",
        type=_positive_int,
        default=2,
        help="Stop after this many consecutive cycles with no measurable progress.",
    )
    exhausted_scope_policy = loop_parser.add_mutually_exclusive_group()
    exhausted_scope_policy.add_argument(
        "--allow-plan-repair-limit",
        action="store_true",
        help=(
            "Explicitly continue resolve_gaps after the Planner max_rounds gate; "
            "the override is recorded in the loop manifest."
        ),
    )
    exhausted_scope_policy.add_argument(
        "--advance-with-documented-gaps",
        action="store_true",
        help=(
            "After exhausting selected-scope repairs, preserve its gaps and "
            "research the next unevaluated task batch."
        ),
    )
    loop_parser.add_argument(
        "--max-follow-ups",
        type=_positive_int,
        default=30,
        help="Resolver follow-up ceiling per cycle (default: 30).",
    )
    loop_parser.add_argument(
        "--max-source-actions",
        type=_positive_int,
        default=10,
        help="Resolver known-source action ceiling per cycle (default: 10).",
    )
    loop_parser.add_argument(
        "--max-search-tasks",
        type=_positive_int,
        default=5,
        help="Maximum new plan tasks introduced per cycle (default: 5).",
    )
    loop_parser.add_argument(
        "--max-queries-per-item",
        type=_positive_int,
        default=3,
        help="Maximum Resolver queries retained per work item (default: 3).",
    )
    loop_parser.add_argument(
        "--max-search-calls",
        type=_positive_int,
        default=10,
        help="Executor paid web-search tool-call ceiling per cycle (default: 10).",
    )
    loop_parser.add_argument(
        "--min-queries-per-task",
        type=_positive_int,
        default=1,
        help=(
            "Minimum exact Resolver queries executed per search task in each "
            "cycle (default: 1)."
        ),
    )
    loop_parser.add_argument(
        "--max-candidate-routes",
        type=_nonnegative_int,
        default=5,
        help="Maximum deterministic Candidate Router promotions per cycle (default: 5).",
    )
    loop_parser.add_argument(
        "--max-extractor-api-calls",
        type=_positive_int,
        default=20,
        help="Executor paid Extractor request ceiling per cycle (default: 20).",
    )
    loop_parser.add_argument(
        "--max-checker-claims",
        type=_positive_int,
        default=500,
        help="Maximum claims audited by each Checker pass (default: 500).",
    )
    loop_parser.add_argument(
        "--max-checker-evidence-chars",
        type=_positive_int,
        default=500_000,
        help="Maximum evidence characters supplied to each Checker (default: 500000).",
    )
    loop_parser.add_argument(
        "--max-document-bytes",
        type=_positive_int,
        default=40 * 1024 * 1024,
        help="Executor hard per-document download cap (default: 40 MiB).",
    )
    loop_parser.add_argument(
        "--max-document-chars",
        type=_positive_int,
        default=250_000,
        help="Maximum selected text stored per document (default: 250000).",
    )
    loop_parser.add_argument(
        "--max-pdf-scan-chars",
        type=_positive_int,
        default=2_000_000,
        help="Maximum locally parsed PDF text before selection (default: 2000000).",
    )
    loop_parser.add_argument(
        "--max-passages-per-task",
        type=_positive_int,
        default=6,
        help="Maximum evidence passages per task/document (default: 6).",
    )
    loop_parser.add_argument(
        "--max-evidence-chars-per-extractor-call",
        type=_positive_int,
        default=100_000,
        help="Hard evidence cap per paid Extractor request (default: 100000).",
    )
    loop_parser.add_argument(
        "--normalize-incomplete",
        action="store_true",
        help=(
            "After a non-budget stop, explicitly create an incomplete paid "
            "Normalizer draft for Human Review."
        ),
    )
    loop_parser.add_argument(
        "--skip-normalize",
        action="store_true",
        help="Do not automatically normalize even when the final Checker passes.",
    )
    loop_parser.add_argument(
        "--max-normalizer-claims",
        type=_positive_int,
        default=500,
        help="Maximum accepted claims normalized after the loop (default: 500).",
    )
    loop_parser.add_argument(
        "--max-normalizer-input-chars",
        type=_positive_int,
        default=500_000,
        help="Maximum Normalizer value/evidence input characters (default: 500000).",
    )
    loop_parser.add_argument(
        "--model",
        help="Override OPENAI_MODEL for all paid stages in this invocation.",
    )

    normalize_parser = subparsers.add_parser(
        "normalize",
        help="Normalize accepted Checker claims into a review-only staging record.",
    )
    normalize_parser.add_argument(
        "--check",
        type=Path,
        required=True,
        help="Exact successful paid Checker artifact to consume.",
    )
    normalize_parser.add_argument(
        "--plan",
        type=Path,
        help="Exact plan artifact; defaults to Checker's plan_reference.",
    )
    normalize_parser.add_argument(
        "--sources",
        type=Path,
        help="Exact Searcher artifact; defaults to Checker's search_reference.",
    )
    normalize_parser.add_argument(
        "--extractions",
        type=Path,
        help="Exact Extractor artifact; defaults to Checker's extraction_reference.",
    )
    normalize_parser.add_argument(
        "--free",
        "--offline",
        dest="offline",
        action="store_true",
        help="Preserve accepted values as deterministic text without OpenAI.",
    )
    normalize_parser.add_argument(
        "--iteration",
        type=_positive_int,
        help="Logical Normalizer iteration; defaults to the Checker iteration.",
    )
    normalize_parser.add_argument(
        "--max-claims",
        type=_positive_int,
        default=100,
        help="Hard accepted-claim ceiling for one normalization pass (default: 100).",
    )
    normalize_parser.add_argument(
        "--max-input-chars",
        type=_positive_int,
        default=100_000,
        help="Hard raw-value and quote character ceiling (default: 100000).",
    )
    normalize_parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help=(
            "Explicitly create a review-only draft from a Checker that did not "
            "pass; gaps remain unresolved and publication stays forbidden."
        ),
    )
    normalize_parser.add_argument(
        "--model", help="Override OPENAI_MODEL for this Normalizer invocation."
    )
    normalize_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Artifact directory; defaults to the Checker directory.",
    )

    review_parser = subparsers.add_parser(
        "review",
        help="Create a readable Human Review report and immutable decision artifact.",
    )
    review_parser.add_argument(
        "--normalized",
        type=Path,
        required=True,
        help="Exact Normalizer artifact to inspect and decide.",
    )
    review_parser.add_argument(
        "--decision",
        choices=[decision.value for decision in HumanReviewDecision],
        default=HumanReviewDecision.PENDING.value,
        help="Review decision (default: pending report only).",
    )
    review_parser.add_argument(
        "--reviewer",
        help="Human reviewer name or stable team identifier; required for a final decision.",
    )
    review_parser.add_argument(
        "--notes",
        default="",
        help="Human review notes stored in the immutable decision artifact.",
    )
    review_parser.add_argument(
        "--acknowledge-incomplete",
        action="store_true",
        help="Explicit acknowledgement required for approved_with_gaps.",
    )
    review_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Artifact directory; defaults to the Normalizer directory.",
    )

    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help=(
            "Repair an existing Executor extraction merge offline while preserving "
            "both input artifacts."
        ),
    )
    reconcile_parser.add_argument(
        "--extractions",
        type=Path,
        required=True,
        help="Current Executor extraction artifact to reconcile.",
    )
    reconcile_parser.add_argument(
        "--prior-extractions",
        type=Path,
        help="Exact predecessor extraction; defaults to current artifact lineage.",
    )
    reconcile_parser.add_argument(
        "--plan",
        type=Path,
        help="Exact plan artifact; defaults to current artifact lineage.",
    )
    reconcile_parser.add_argument(
        "--sources",
        type=Path,
        help="Exact merged Searcher artifact; defaults to current artifact lineage.",
    )
    reconcile_parser.add_argument(
        "--resolution",
        type=Path,
        help="Exact Resolver artifact; defaults to current artifact lineage.",
    )
    reconcile_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Artifact directory; defaults to current extraction directory.",
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
                max_candidate_routes=args.max_candidate_routes,
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
        "provider_tool_call_overrun": results.provider_tool_call_overrun,
        "action_candidate_urls": len(action_candidate_urls),
        "candidate_routes": {
            decision: sum(
                route.decision == decision for route in results.candidate_routes
            )
            for decision in ("routed", "unassigned", "excluded", "limit_reached")
        },
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
    incremental = bool(getattr(args, "incremental", False))
    if incremental and args.offline:
        raise CheckerValidationError(
            "--incremental cannot be combined with --free; changed scope requires paid review."
        )
    prior_checker_results = None
    prior_checker_sha256 = None
    prior_checker_reference = None
    prior_extraction_results = None
    prior_extraction_sha256 = None
    prior_search_results = None
    prior_search_sha256 = None
    if incremental:
        if (
            extraction_results.generated_by != "executor"
            or extraction_results.resolution_reference is None
            or extraction_results.prior_extraction_reference is None
        ):
            raise CheckerValidationError(
                "Incremental Checker requires Executor resolution and predecessor extraction lineage."
            )
        resolution, resolution_sha256 = load_resolver_results(
            extraction_results.resolution_reference
        )
        if (
            extraction_results.resolution_id != resolution.resolution_id
            or extraction_results.resolution_sha256 != resolution_sha256
        ):
            raise CheckerValidationError(
                "Executor resolution lineage does not match exact artifact bytes."
            )
        prior_checker_reference = str(Path(resolution.check_reference).resolve())
        prior_checker_results, prior_checker_sha256 = load_checker_results(
            prior_checker_reference
        )
        if (
            resolution.check_id != prior_checker_results.check_id
            or resolution.check_sha256 != prior_checker_sha256
        ):
            raise CheckerValidationError(
                "Resolver Checker lineage does not match exact artifact bytes."
            )
        prior_extraction_results, prior_extraction_sha256 = load_extraction_results(
            extraction_results.prior_extraction_reference
        )
        if (
            extraction_results.prior_extraction_id
            != prior_extraction_results.extraction_id
            or extraction_results.prior_extraction_sha256
            != prior_extraction_sha256
        ):
            raise CheckerValidationError(
                "Executor predecessor extraction lineage does not match exact bytes."
            )
        prior_search_results, prior_search_sha256 = load_search_results(
            prior_checker_results.search_reference
        )
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
                prior_checker_results=prior_checker_results,
                prior_checker_sha256=prior_checker_sha256,
                prior_checker_reference=prior_checker_reference,
                prior_extraction_results=prior_extraction_results,
                prior_extraction_sha256=prior_extraction_sha256,
                prior_search_results=prior_search_results,
                prior_search_sha256=prior_search_sha256,
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
        "checker_mode": results.checker_mode.value,
        "reviewed_tasks": len(results.reviewed_task_ids),
        "reviewed_sources": len(results.reviewed_source_ids),
        "reviewed_claims": len(results.reviewed_claim_ids),
        "inherited_claims": len(results.inherited_claim_ids),
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


def _consecutive_gap_repair_rounds(extraction_results) -> int:
    """Count exact predecessor executions planned from resolve_gaps checks."""

    rounds = 0
    current = extraction_results
    seen_extraction_ids: set[str] = set()
    while current.generated_by == "executor":
        if current.extraction_id in seen_extraction_ids:
            raise ResolverValidationError(
                "Executor predecessor lineage contains a cycle."
            )
        seen_extraction_ids.add(current.extraction_id)
        if current.resolution_reference is None:
            raise ResolverValidationError(
                "Executor extraction is missing Resolver lineage."
            )
        resolution, resolution_sha256 = load_resolver_results(
            current.resolution_reference
        )
        if (
            current.resolution_id != resolution.resolution_id
            or current.resolution_sha256 != resolution_sha256
        ):
            raise ResolverValidationError(
                "Executor extraction Resolver lineage does not match exact bytes."
            )
        prior_check, prior_check_sha256 = load_checker_results(
            resolution.check_reference
        )
        if (
            resolution.check_id != prior_check.check_id
            or resolution.check_sha256 != prior_check_sha256
        ):
            raise ResolverValidationError(
                "Resolver Checker lineage does not match exact bytes."
            )
        if (
            prior_check.recommended_next_action
            != CheckerNextAction.RESOLVE_GAPS
        ):
            break
        rounds += 1
        if current.prior_extraction_reference is None:
            break
        prior, prior_sha256 = load_extraction_results(
            current.prior_extraction_reference
        )
        if (
            current.prior_extraction_id != prior.extraction_id
            or current.prior_extraction_sha256 != prior_sha256
        ):
            raise ResolverValidationError(
                "Executor predecessor extraction lineage does not match exact bytes."
            )
        current = prior
    return rounds


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
    completed_gap_rounds = _consecutive_gap_repair_rounds(
        extraction_results
    )
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
                completed_gap_rounds=completed_gap_rounds,
                allow_round_limit=args.allow_round_limit,
                force_scope_expansion=args.advance_with_documented_gaps,
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
        "scope_expansion_override": results.scope_expansion_override,
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
                max_candidate_routes=args.max_candidate_routes,
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
            fallback_model = (
                search_llm.model_name
                if search_llm is not None
                else args.model or "unknown"
            )
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
            if not paths:
                paths.append(
                    save_agent_failure(
                        AgentFailureArtifact(
                            failure_id=str(uuid4()),
                            plan_run_id=plan.run_id,
                            created_at=datetime.now(timezone.utc),
                            error_code=exc.code,
                            agent=exc.agent or "searcher",
                            iteration=exc.iteration or iteration,
                            call_index=exc.call_index or 1,
                            scope_task_ids=(
                                exc.scope_task_ids or resolution.search_task_ids
                            ),
                            requested_model=exc.requested_model or fallback_model,
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
        "provider_tool_call_overrun": (
            merged_search.provider_tool_call_overrun
        ),
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
        "candidate_routes": {
            decision: sum(
                route.decision == decision
                for route in merged_search.candidate_routes
            )
            for decision in ("routed", "unassigned", "excluded", "limit_reached")
        },
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
            f"{' --incremental' if results.execution_mode == ExecutorMode.PAID else ''}"
            f" --max-claims {max(100, len(merged_extraction.claims))}"
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _save_normalizer_failure_ledger(results, reference_path: Path) -> Path:
    """Preserve the one paid Normalizer attempt if publication fails."""

    usage = results.agent_usage[0] if results.agent_usage else None
    attempt = results.failed_attempts[0] if results.failed_attempts else None
    failure = AgentFailureArtifact(
        failure_id=str(uuid4()),
        plan_run_id=results.plan_run_id,
        created_at=datetime.now(timezone.utc),
        error_code=(
            attempt.error_code if attempt is not None else "artifact_write_failed"
        ),
        agent="normalizer",
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
        requested_model=results.model or "unknown",
        usage=usage,
        observed_tool_calls=0,
        tool_usage=[],
        token_usage_unknown=usage is None,
    )
    return save_agent_failure(failure, reference_path)


def _run_normalize(args: argparse.Namespace) -> int:
    checker_results, check_sha256 = load_checker_results(args.check)
    if checker_results.checker_mode == CheckerMode.INCREMENTAL:
        raise NormalizerValidationError(
            "Normalizer requires a full Checker artifact. Run `datacollector check` "
            "without --incremental against the same extraction first."
        )
    if not checker_results.passed and not args.allow_incomplete:
        raise NormalizerValidationError(
            "Checker did not pass. Review its documented gaps and rerun with "
            "--allow-incomplete only when an incomplete staging draft is intended."
        )
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
    expected_path = result_directory / normalizer_results_filename_for(
        iteration,
        free=args.offline,
    )

    with reserve_artifact(expected_path):
        llm = None
        if not args.offline:
            settings = OpenAISettings.from_env()
            if args.model:
                settings = replace(settings, model=args.model)
            llm = OpenAINormalizerClient(settings)
        results = NormalizerAgent(llm).create_normalizer_results(
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
            mode=(NormalizerMode.FREE if args.offline else NormalizerMode.PAID),
            allow_incomplete=args.allow_incomplete,
            max_claims=args.max_claims,
            max_input_chars=args.max_input_chars,
        )
        try:
            results_path = save_normalizer_results(
                results,
                args.check,
                output_dir=args.output_dir,
            )
        except Exception as exc:
            if not results.provider_executed:
                raise
            ledger_note = ""
            try:
                failure_path = _save_normalizer_failure_ledger(
                    results,
                    args.check,
                )
            except Exception as ledger_exc:
                ledger_note = (
                    " Normalizer failure ledger also failed with "
                    f"{type(ledger_exc).__name__}."
                )
            else:
                ledger_note = f" Provider usage saved to: {failure_path}."
            raise NormalizerProviderError(
                "Paid Normalizer completed its provider attempt but its final "
                f"artifact could not be saved ({type(exc).__name__}).{ledger_note}",
                code="artifact_write_failed",
                usage=results.agent_usage[0] if results.agent_usage else None,
                iteration=results.iteration,
                scope_task_ids=(
                    results.agent_usage[0].scope_task_ids
                    if results.agent_usage
                    else results.failed_attempts[0].scope_task_ids
                ),
                scope_source_ids=(
                    results.agent_usage[0].scope_source_ids
                    if results.agent_usage
                    else results.failed_attempts[0].scope_source_ids
                ),
                requested_model=results.model,
            ) from None

    field_statuses: dict[str, int] = {}
    value_types: dict[str, int] = {}
    for field in results.field_results:
        field_statuses[field.status.value] = (
            field_statuses.get(field.status.value, 0) + 1
        )
    for value in results.normalized_values:
        value_types[value.value_type.value] = (
            value_types.get(value.value_type.value, 0) + 1
        )
    summary = {
        "normalization_id": results.normalization_id,
        "plan_run_id": results.plan_run_id,
        "search_id": results.search_id,
        "extraction_id": results.extraction_id,
        "check_id": results.check_id,
        "check_sha256": results.check_sha256,
        "brand": results.brand_name,
        "normalization_mode": results.normalization_mode.value,
        "generated_by": results.generated_by,
        "strategy_source": results.strategy_source.value,
        "model": results.model,
        "iteration": results.iteration,
        "provider_executed": results.provider_executed,
        "input_checker_passed": results.input_checker_passed,
        "incomplete_input_allowed": results.incomplete_input_allowed,
        "input_quality_score": results.input_quality_score,
        "input_scope_complete": results.input_scope_complete,
        "eligible_claims": len(results.eligible_claim_ids),
        "excluded_claims": len(results.excluded_claim_ids),
        "normalized_values": len(results.normalized_values),
        "repair_summary": results.repair_summary.model_dump(mode="json"),
        "field_statuses": field_statuses,
        "value_types": value_types,
        "unresolved_fields": len(results.unresolved_field_ids),
        "critical_missing_fields": len(results.critical_missing_fields),
        "unevaluated_critical_fields": len(
            results.unevaluated_critical_fields
        ),
        "publishable": results.publishable,
        "ready_for_human_review": results.ready_for_human_review,
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
        "normalized_path": str(results_path),
        "next_step": "human_review",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _invoke_loop_stage(handler, args: argparse.Namespace) -> dict[str, object]:
    """Run an existing immutable CLI stage while retaining one final JSON output."""

    output = StringIO()
    with redirect_stdout(output):
        exit_code = handler(args)
    if exit_code != 0:
        raise LoopValidationError(
            f"Orchestrated stage {handler.__name__} returned {exit_code}."
        )
    rendered = output.getvalue().strip()
    if not rendered:
        raise LoopValidationError(
            f"Orchestrated stage {handler.__name__} returned no summary."
        )
    parsed = json.loads(rendered)
    if not isinstance(parsed, dict):
        raise LoopValidationError(
            f"Orchestrated stage {handler.__name__} returned invalid JSON."
        )
    return parsed


def _next_loop_iteration(directory: Path, minimum: int) -> int:
    """Allocate a monotonic iteration above every artifact already in the run."""

    observed = [minimum]
    pattern = re.compile(r"-r(\d+)(?:-|\.)")
    for artifact in directory.glob("*.json"):
        match = pattern.search(artifact.name)
        if match is not None:
            observed.append(int(match.group(1)))
    return max(observed) + 1


def _loop_stage_usage(
    stage: str,
    iteration: int,
    artifact_reference: Path,
    summary: dict[str, object],
) -> LoopStageUsage:
    raw_totals = summary.get("usage_totals")
    if not isinstance(raw_totals, dict):
        raise LoopValidationError(
            f"Orchestrated {stage} summary omitted usage_totals."
        )
    estimated = raw_totals.get("estimated_cost_usd")
    api_attempts = int(raw_totals.get("api_attempts_recorded", 0))
    api_calls_with_usage = int(raw_totals.get("api_calls_with_usage", 0))
    return LoopStageUsage(
        stage=stage,
        iteration=iteration,
        artifact_reference=str(artifact_reference.resolve()),
        api_attempts_recorded=api_attempts,
        api_calls_with_usage=api_calls_with_usage,
        input_tokens=int(raw_totals.get("input_tokens", 0)),
        output_tokens=int(raw_totals.get("output_tokens", 0)),
        reasoning_tokens=int(raw_totals.get("reasoning_tokens", 0)),
        total_tokens=int(raw_totals.get("total_tokens", 0)),
        tool_calls=int(raw_totals.get("tool_calls", 0)),
        tool_cost_usd=Decimal(str(raw_totals.get("tool_cost_usd", "0"))),
        estimated_cost_usd=(
            Decimal(str(estimated)) if estimated is not None else None
        ),
        token_usage_unknown=(
            estimated is None and api_attempts > api_calls_with_usage
        ),
    )


def _loop_stages(
    rounds: list[LoopRoundResult],
    post_loop_usage: list[LoopStageUsage] | None = None,
) -> list[LoopStageUsage]:
    return [
        *(stage for round_result in rounds for stage in round_result.stage_usage),
        *(post_loop_usage or []),
    ]


def _loop_usage_totals(stages: list[LoopStageUsage]) -> dict[str, object]:
    known_costs = [stage.estimated_cost_usd for stage in stages]
    estimated_cost = (
        sum((cost for cost in known_costs if cost is not None), Decimal("0"))
        if all(cost is not None for cost in known_costs)
        else None
    )
    return {
        "api_attempts": sum(stage.api_attempts_recorded for stage in stages),
        "input_tokens": sum(stage.input_tokens for stage in stages),
        "output_tokens": sum(stage.output_tokens for stage in stages),
        "reasoning_tokens": sum(stage.reasoning_tokens for stage in stages),
        "total_tokens": sum(stage.total_tokens for stage in stages),
        "tool_calls": sum(stage.tool_calls for stage in stages),
        "tool_cost_usd": sum(
            (stage.tool_cost_usd for stage in stages), Decimal("0")
        ),
        "estimated_cost_usd": estimated_cost,
    }


def _loop_progress(
    before,
    after,
    *,
    min_quality_improvement: int,
) -> tuple[bool, list[str], list[str]]:
    reasons: list[str] = []
    regressions: list[str] = []
    quality_delta = after.quality_score - before.quality_score
    critical_delta = len(after.critical_missing_fields) - len(
        before.critical_missing_fields
    )
    contradiction_delta = len(after.contradictions) - len(before.contradictions)
    before_verified = sum(
        field.status.value == "verified"
        for task in before.task_results
        for field in task.field_results
    )
    after_verified = sum(
        field.status.value == "verified"
        for task in after.task_results
        for field in task.field_results
    )
    verified_delta = after_verified - before_verified
    if (
        quality_delta > 0
        and quality_delta >= min_quality_improvement
        and critical_delta <= 0
    ):
        reasons.append(f"quality_score+{quality_delta}")
    if critical_delta < 0:
        reasons.append(f"critical_missing{critical_delta}")
    elif critical_delta > 0:
        regressions.append(f"critical_missing+{critical_delta}")
    if contradiction_delta < 0:
        reasons.append(f"contradictions{contradiction_delta}")
    elif contradiction_delta > 0:
        regressions.append(f"contradictions+{contradiction_delta}")
    if verified_delta > 0:
        reasons.append(f"verified_fields+{verified_delta}")
    elif verified_delta < 0:
        regressions.append(f"verified_fields{verified_delta}")
    evaluated_task_increase = len(after.selected_task_ids) - len(
        before.selected_task_ids
    )
    if evaluated_task_increase > 0:
        reasons.append(f"evaluated_tasks+{evaluated_task_increase}")
    if not before.selected_scope_ready and after.selected_scope_ready:
        reasons.append("selected_scope_ready")
    if quality_delta < 0:
        regressions.append(f"quality_score{quality_delta}")
    return bool(reasons), reasons, regressions


def _make_loop_round(
    *,
    number: int,
    action: CheckerNextAction | str,
    before,
    before_path: Path,
    before_sha256: str,
    after,
    after_path: Path,
    after_sha256: str,
    resolution_path: Path | None,
    execution_path: Path | None,
    stage_usage: list[LoopStageUsage],
    min_quality_improvement: int,
) -> LoopRoundResult:
    progress, reasons, regressions = _loop_progress(
        before,
        after,
        min_quality_improvement=min_quality_improvement,
    )
    return LoopRoundResult(
        round_number=number,
        checker_action=(action.value if isinstance(action, CheckerNextAction) else action),
        starting_check_id=before.check_id,
        starting_check_reference=str(before_path.resolve()),
        starting_check_sha256=before_sha256,
        ending_check_id=after.check_id,
        ending_check_reference=str(after_path.resolve()),
        ending_check_sha256=after_sha256,
        resolution_reference=(
            str(resolution_path.resolve()) if resolution_path is not None else None
        ),
        execution_reference=(
            str(execution_path.resolve()) if execution_path is not None else None
        ),
        quality_before=before.quality_score,
        quality_after=after.quality_score,
        quality_delta=after.quality_score - before.quality_score,
        evaluated_tasks_before=len(before.selected_task_ids),
        evaluated_tasks_after=len(after.selected_task_ids),
        evaluated_sources_before=len(before.selected_source_ids),
        evaluated_sources_after=len(after.selected_source_ids),
        evaluated_claims_before=len(before.selected_claim_ids),
        evaluated_claims_after=len(after.selected_claim_ids),
        critical_missing_before=len(before.critical_missing_fields),
        critical_missing_after=len(after.critical_missing_fields),
        contradictions_before=len(before.contradictions),
        contradictions_after=len(after.contradictions),
        verified_fields_before=sum(
            field.status.value == "verified"
            for task in before.task_results
            for field in task.field_results
        ),
        verified_fields_after=sum(
            field.status.value == "verified"
            for task in after.task_results
            for field in task.field_results
        ),
        selected_scope_ready_before=before.selected_scope_ready,
        selected_scope_ready_after=after.selected_scope_ready,
        progress_detected=progress,
        progress_reasons=reasons,
        regression_reasons=regressions,
        stage_usage=stage_usage,
    )


def _run_loop(args: argparse.Namespace) -> int:
    """Run bounded paid repair and scope-expansion cycles from one Checker."""

    started_at = datetime.now(timezone.utc)
    initial_path = args.check.resolve()
    initial_check, initial_check_sha256 = load_checker_results(initial_path)
    plan_path = Path(initial_check.plan_reference)
    plan, plan_sha256 = load_research_plan(plan_path)
    if (
        initial_check.plan_run_id != plan.run_id
        or initial_check.plan_sha256 != plan_sha256
    ):
        raise CheckerValidationError(
            "Loop starting Checker does not match its exact Planner artifact."
        )

    policy = LoopPolicy(
        quality_threshold=initial_check.quality_threshold,
        max_rounds=args.max_rounds,
        max_estimated_cost_usd=args.max_cost_usd,
        min_quality_improvement=args.min_quality_improvement,
        max_stagnant_rounds=args.max_stagnant_rounds,
        allow_plan_repair_limit=args.allow_plan_repair_limit,
        advance_with_documented_gaps=args.advance_with_documented_gaps,
    )
    run_directory = initial_path.parent
    next_iteration = _next_loop_iteration(
        run_directory,
        minimum=initial_check.iteration,
    )
    current = initial_check
    current_path = initial_path
    current_sha256 = initial_check_sha256
    rounds: list[LoopRoundResult] = []
    stagnant_rounds = 0
    stop_reason: LoopStopReason | None = None
    warnings = [
        "The cost ceiling is evaluated between complete agent cycles; the final "
        "in-flight cycle can make recorded cost exceed the configured ceiling."
    ]
    if policy.allow_plan_repair_limit:
        warnings.append(
            "A human explicitly allowed resolve_gaps beyond the Planner repair-round "
            "limit for this orchestration session."
        )
    if policy.advance_with_documented_gaps:
        warnings.append(
            "A human explicitly allowed progression to unevaluated plan tasks while "
            "retaining exhausted selected-scope gaps as unresolved."
        )

    while len(rounds) < policy.max_rounds:
        if current.passed:
            stop_reason = LoopStopReason.CHECKER_PASSED
            break

        stages_before = _loop_stages(rounds)
        totals_before = _loop_usage_totals(stages_before)
        known_cost_before = totals_before["estimated_cost_usd"]
        if known_cost_before is None:
            stop_reason = LoopStopReason.COST_UNKNOWN
            break
        if known_cost_before >= policy.max_estimated_cost_usd:
            stop_reason = LoopStopReason.BUDGET_EXHAUSTED
            break

        action = current.recommended_next_action
        force_scope_expansion = False
        if action == CheckerNextAction.RESOLVE_GAPS:
            extraction, _ = load_extraction_results(current.extraction_reference)
            repair_rounds = _consecutive_gap_repair_rounds(extraction)
            if repair_rounds >= plan.stop_conditions.max_rounds:
                force_scope_expansion = (
                    policy.advance_with_documented_gaps
                    and bool(
                        current.unevaluated_task_ids
                        or current.unevaluated_source_ids
                    )
                )
                if not policy.allow_plan_repair_limit and not force_scope_expansion:
                    stop_reason = LoopStopReason.PLAN_REPAIR_LIMIT
                    break

        before = current
        before_path = current_path
        before_sha256 = current_sha256
        stage_usage: list[LoopStageUsage] = []
        resolution_path: Path | None = None
        execution_path: Path | None = None

        if action in {
            CheckerNextAction.RUN_PAID_CHECKER,
            CheckerNextAction.RETRY_CHECKER,
        }:
            check_iteration = next_iteration
            check_summary = _invoke_loop_stage(
                _run_check,
                argparse.Namespace(
                    extractions=Path(current.extraction_reference),
                    plan=None,
                    sources=None,
                    offline=False,
                    incremental=False,
                    iteration=check_iteration,
                    max_claims=args.max_checker_claims,
                    max_evidence_chars=args.max_checker_evidence_chars,
                    model=args.model,
                ),
            )
            current_path = Path(str(check_summary["check_path"]))
            current, current_sha256 = load_checker_results(current_path)
            stage_usage.append(
                _loop_stage_usage(
                    "checker",
                    check_iteration,
                    current_path,
                    check_summary,
                )
            )
            next_iteration = check_iteration + 1
        elif action in {
            CheckerNextAction.RESOLVE_GAPS,
            CheckerNextAction.RESEARCH_NEXT_BATCH,
        }:
            resolution_iteration = next_iteration
            resolution_summary = _invoke_loop_stage(
                _run_resolve,
                argparse.Namespace(
                    check=current_path,
                    plan=None,
                    sources=None,
                    extractions=None,
                    offline=False,
                    iteration=resolution_iteration,
                    max_follow_ups=args.max_follow_ups,
                    max_source_actions=args.max_source_actions,
                    max_search_tasks=args.max_search_tasks,
                    max_queries_per_item=args.max_queries_per_item,
                    model=args.model,
                    allow_round_limit=policy.allow_plan_repair_limit,
                    advance_with_documented_gaps=force_scope_expansion,
                    output_dir=None,
                ),
            )
            resolution_path = Path(str(resolution_summary["resolution_path"]))
            resolution, _ = load_resolver_results(resolution_path)
            stage_usage.append(
                _loop_stage_usage(
                    "resolver",
                    resolution_iteration,
                    resolution_path,
                    resolution_summary,
                )
            )
            next_iteration = resolution_iteration + 1

            if resolution.ready_for_execution:
                execution_iteration = next_iteration
                execution_summary = _invoke_loop_stage(
                    _run_execute,
                    argparse.Namespace(
                        resolution=resolution_path,
                        plan=None,
                        sources=None,
                        extractions=None,
                        check=None,
                        offline=False,
                        iteration=execution_iteration,
                        max_search_calls=args.max_search_calls,
                        min_queries_per_task=args.min_queries_per_task,
                        max_retry_tasks=0,
                        retry_search_calls=1,
                        max_candidate_routes=args.max_candidate_routes,
                        max_document_bytes=args.max_document_bytes,
                        max_document_chars=args.max_document_chars,
                        max_pdf_scan_chars=args.max_pdf_scan_chars,
                        max_passages_per_task=args.max_passages_per_task,
                        max_evidence_chars_per_call=(
                            args.max_evidence_chars_per_extractor_call
                        ),
                        max_extractor_api_calls=args.max_extractor_api_calls,
                        model=args.model,
                        output_dir=None,
                    ),
                )
                execution_path = Path(str(execution_summary["execution_path"]))
                execution, _ = load_executor_results(execution_path)
                stage_usage.append(
                    _loop_stage_usage(
                        "executor",
                        execution_iteration,
                        execution_path,
                        execution_summary,
                    )
                )
                next_iteration = execution_iteration + 1

                if execution.ready_for_checker:
                    current_path = Path(str(execution_summary["extractions_path"]))
                    check_summary = _invoke_loop_stage(
                        _run_check,
                        argparse.Namespace(
                            extractions=current_path,
                            plan=None,
                            sources=None,
                            offline=False,
                            incremental=True,
                            iteration=execution_iteration,
                            max_claims=args.max_checker_claims,
                            max_evidence_chars=args.max_checker_evidence_chars,
                            model=args.model,
                        ),
                    )
                    current_path = Path(str(check_summary["check_path"]))
                    current, current_sha256 = load_checker_results(current_path)
                    stage_usage.append(
                        _loop_stage_usage(
                            "checker",
                            execution_iteration,
                            current_path,
                            check_summary,
                        )
                    )
                else:
                    stop_reason = LoopStopReason.HUMAN_REVIEW_REQUIRED
            else:
                stop_reason = LoopStopReason.HUMAN_REVIEW_REQUIRED
        else:
            stop_reason = LoopStopReason.HUMAN_REVIEW_REQUIRED
            break

        round_result = _make_loop_round(
            number=len(rounds) + 1,
            action=(
                "advance_with_documented_gaps"
                if force_scope_expansion
                else action
            ),
            before=before,
            before_path=before_path,
            before_sha256=before_sha256,
            after=current,
            after_path=current_path,
            after_sha256=current_sha256,
            resolution_path=resolution_path,
            execution_path=execution_path,
            stage_usage=stage_usage,
            min_quality_improvement=policy.min_quality_improvement,
        )
        rounds.append(round_result)
        stagnant_rounds = 0 if round_result.progress_detected else stagnant_rounds + 1

        if stop_reason is not None:
            break
        if current.passed:
            stop_reason = LoopStopReason.CHECKER_PASSED
            break
        totals_after = _loop_usage_totals(_loop_stages(rounds))
        known_cost_after = totals_after["estimated_cost_usd"]
        if known_cost_after is None:
            stop_reason = LoopStopReason.COST_UNKNOWN
            break
        if known_cost_after >= policy.max_estimated_cost_usd:
            stop_reason = LoopStopReason.BUDGET_EXHAUSTED
            break
        if stagnant_rounds >= policy.max_stagnant_rounds:
            stop_reason = LoopStopReason.NO_PROGRESS
            break

    if stop_reason is None:
        stop_reason = (
            LoopStopReason.CHECKER_PASSED
            if current.passed
            else LoopStopReason.MAX_ROUNDS
        )

    post_loop_usage: list[LoopStageUsage] = []
    normalization_reference: str | None = None
    normalization_requested = (
        current.passed and not args.skip_normalize
    ) or (
        not current.passed
        and args.normalize_incomplete
        and stop_reason
        not in {LoopStopReason.BUDGET_EXHAUSTED, LoopStopReason.COST_UNKNOWN}
    )
    if (
        normalization_requested
        and current.checker_mode == CheckerMode.INCREMENTAL
    ):
        cost_before_full_check = _loop_usage_totals(_loop_stages(rounds))[
            "estimated_cost_usd"
        ]
        if (
            cost_before_full_check is not None
            and cost_before_full_check < policy.max_estimated_cost_usd
        ):
            full_check_iteration = _next_loop_iteration(
                run_directory,
                minimum=next_iteration - 1,
            )
            full_check_summary = _invoke_loop_stage(
                _run_check,
                argparse.Namespace(
                    extractions=Path(current.extraction_reference),
                    plan=None,
                    sources=None,
                    offline=False,
                    incremental=False,
                    iteration=full_check_iteration,
                    max_claims=args.max_checker_claims,
                    max_evidence_chars=args.max_checker_evidence_chars,
                    model=args.model,
                ),
            )
            current_path = Path(str(full_check_summary["check_path"]))
            current, current_sha256 = load_checker_results(current_path)
            if (
                not current.passed
                and stop_reason == LoopStopReason.CHECKER_PASSED
            ):
                stop_reason = LoopStopReason.MAX_ROUNDS
            post_loop_usage.append(
                _loop_stage_usage(
                    "checker_full_pre_normalizer",
                    full_check_iteration,
                    current_path,
                    full_check_summary,
                )
            )
            next_iteration = full_check_iteration + 1
            warnings.append(
                "A full paid Checker pass replaced the incremental decision before Normalizer."
            )
        else:
            warnings.append(
                "Normalizer was not started because a mandatory full Checker pass "
                "could not start within the known loop cost ceiling."
            )

    stages_before_normalizer = _loop_stages(rounds, post_loop_usage)
    cost_before_normalizer = _loop_usage_totals(stages_before_normalizer)[
        "estimated_cost_usd"
    ]
    budget_allows_normalizer = (
        cost_before_normalizer is not None
        and cost_before_normalizer < policy.max_estimated_cost_usd
    )
    should_normalize = current.checker_mode == CheckerMode.FULL and (
        (
        current.passed and not args.skip_normalize and budget_allows_normalizer
        ) or (
        not current.passed
        and args.normalize_incomplete
        and stop_reason
        not in {LoopStopReason.BUDGET_EXHAUSTED, LoopStopReason.COST_UNKNOWN}
        and budget_allows_normalizer
        )
    )
    if should_normalize:
        normalizer_iteration = _next_loop_iteration(
            run_directory,
            minimum=next_iteration - 1,
        )
        normalizer_summary = _invoke_loop_stage(
            _run_normalize,
            argparse.Namespace(
                check=current_path,
                plan=None,
                sources=None,
                extractions=None,
                offline=False,
                iteration=normalizer_iteration,
                max_claims=args.max_normalizer_claims,
                max_input_chars=args.max_normalizer_input_chars,
                allow_incomplete=not current.passed,
                model=args.model,
                output_dir=None,
            ),
        )
        normalized_path = Path(str(normalizer_summary["normalized_path"]))
        normalization_reference = str(normalized_path.resolve())
        post_loop_usage.append(
            _loop_stage_usage(
                "normalizer",
                normalizer_iteration,
                normalized_path,
                normalizer_summary,
            )
        )
    elif current.passed and not args.skip_normalize and not budget_allows_normalizer:
        warnings.append(
            "Checker passed, but Normalizer was not started because the loop cost "
            "ceiling had been reached or exact cost was unknown."
        )

    all_stages = _loop_stages(rounds, post_loop_usage)
    totals = _loop_usage_totals(all_stages)
    if (
        totals["estimated_cost_usd"] is not None
        and totals["estimated_cost_usd"] > policy.max_estimated_cost_usd
    ):
        warnings.append(
            "Recorded incremental cost exceeded the configured ceiling during the "
            "last already-started agent cycle."
        )

    if normalization_reference is not None:
        recommended_next_action = LoopNextAction.HUMAN_REVIEW
    elif current.passed:
        recommended_next_action = LoopNextAction.NORMALIZE
    elif stop_reason in {
        LoopStopReason.MAX_ROUNDS,
        LoopStopReason.BUDGET_EXHAUSTED,
    }:
        recommended_next_action = LoopNextAction.RESUME_LOOP
    else:
        recommended_next_action = LoopNextAction.INSPECT_GAPS

    results = LoopRunResults(
        loop_id=str(uuid4()),
        plan_run_id=plan.run_id,
        plan_reference=str(plan_path.resolve()),
        plan_sha256=plan_sha256,
        brand_name=plan.planner_input.brand_name,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        initial_check_id=initial_check.check_id,
        initial_check_reference=str(initial_path),
        initial_check_sha256=initial_check_sha256,
        final_check_id=current.check_id,
        final_check_reference=str(current_path.resolve()),
        final_check_sha256=current_sha256,
        policy=policy,
        rounds=rounds,
        post_loop_usage=post_loop_usage,
        stop_reason=stop_reason,
        final_quality_score=current.quality_score,
        final_quality_threshold=current.quality_threshold,
        final_scope_complete=current.scope_complete,
        final_checker_passed=current.passed,
        incremental_api_attempts=int(totals["api_attempts"]),
        incremental_input_tokens=int(totals["input_tokens"]),
        incremental_output_tokens=int(totals["output_tokens"]),
        incremental_reasoning_tokens=int(totals["reasoning_tokens"]),
        incremental_total_tokens=int(totals["total_tokens"]),
        incremental_tool_calls=int(totals["tool_calls"]),
        incremental_tool_cost_usd=Decimal(str(totals["tool_cost_usd"])),
        incremental_estimated_cost_usd=totals["estimated_cost_usd"],
        normalization_reference=normalization_reference,
        recommended_next_action=recommended_next_action,
        warnings=warnings,
    )
    loop_path = save_loop_results(results, initial_path)
    _, loop_sha256 = load_loop_results(loop_path)

    if normalization_reference is not None:
        next_command = (
            ".venv/bin/python -m datacollector review --normalized "
            f"{normalization_reference}"
        )
    elif recommended_next_action == LoopNextAction.NORMALIZE:
        next_command = (
            ".venv/bin/python -m datacollector normalize --check "
            f"{current_path.resolve()}"
        )
    elif recommended_next_action == LoopNextAction.RESUME_LOOP:
        repair_override = (
            " --allow-plan-repair-limit"
            if policy.allow_plan_repair_limit
            else (
                " --advance-with-documented-gaps"
                if policy.advance_with_documented_gaps
                else ""
            )
        )
        next_command = (
            ".venv/bin/python -m datacollector loop --check "
            f"{current_path.resolve()} --max-rounds {policy.max_rounds} "
            f"--max-cost-usd {policy.max_estimated_cost_usd} "
            f"--max-search-calls {args.max_search_calls} "
            f"--min-queries-per-task {args.min_queries_per_task} "
            f"--max-candidate-routes {args.max_candidate_routes} "
            f"--max-extractor-api-calls {args.max_extractor_api_calls}"
            f"{repair_override}"
        )
    else:
        next_command = None

    summary = {
        "loop_id": results.loop_id,
        "brand": results.brand_name,
        "initial_check_id": results.initial_check_id,
        "final_check_id": results.final_check_id,
        "rounds_completed": len(results.rounds),
        "rounds": [
            {
                "round": item.round_number,
                "action": item.checker_action,
                "quality_before": item.quality_before,
                "quality_after": item.quality_after,
                "quality_delta": item.quality_delta,
                "evaluated_tasks_before": item.evaluated_tasks_before,
                "evaluated_tasks_after": item.evaluated_tasks_after,
                "progress_detected": item.progress_detected,
                "progress_reasons": item.progress_reasons,
                "regression_reasons": item.regression_reasons,
                "critical_missing_before": item.critical_missing_before,
                "critical_missing_after": item.critical_missing_after,
                "contradictions_before": item.contradictions_before,
                "contradictions_after": item.contradictions_after,
                "selected_scope_ready_before": item.selected_scope_ready_before,
                "selected_scope_ready_after": item.selected_scope_ready_after,
                "resolution_path": item.resolution_reference,
                "execution_path": item.execution_reference,
                "check_path": item.ending_check_reference,
            }
            for item in results.rounds
        ],
        "stop_reason": results.stop_reason.value,
        "final_quality_score": results.final_quality_score,
        "quality_threshold": results.final_quality_threshold,
        "scope_complete": results.final_scope_complete,
        "checker_passed": results.final_checker_passed,
        "usage_totals": {
            "api_attempts_recorded": results.incremental_api_attempts,
            "input_tokens": results.incremental_input_tokens,
            "output_tokens": results.incremental_output_tokens,
            "reasoning_tokens": results.incremental_reasoning_tokens,
            "total_tokens": results.incremental_total_tokens,
            "tool_calls": results.incremental_tool_calls,
            "tool_cost_usd": str(results.incremental_tool_cost_usd),
            "estimated_cost_usd": (
                str(results.incremental_estimated_cost_usd)
                if results.incremental_estimated_cost_usd is not None
                else None
            ),
        },
        "normalization_path": results.normalization_reference,
        "recommended_next_action": results.recommended_next_action.value,
        "warnings": results.warnings,
        "loop_path": str(loop_path),
        "loop_sha256": loop_sha256,
        "next_command": next_command,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _run_review(args: argparse.Namespace) -> int:
    normalized, normalized_sha256 = load_normalizer_results(args.normalized)
    plan_path = Path(normalized.plan_reference)
    search_path = Path(normalized.search_reference)
    extraction_path = Path(normalized.extraction_reference)
    check_path = Path(normalized.check_reference)
    plan, plan_sha256 = load_research_plan(plan_path)
    search_results, search_sha256 = load_search_results(search_path)
    extraction_results, extraction_sha256 = load_extraction_results(extraction_path)
    checker_results, check_sha256 = load_checker_results(check_path)
    decision = HumanReviewDecision(args.decision)
    result_directory = args.output_dir or args.normalized.parent
    review_path, report_path = human_review_paths_for(
        normalized.iteration,
        decision.value,
        result_directory,
    )
    reviewer = HumanReviewer()
    with reserve_artifact(review_path), reserve_artifact(report_path):
        results = reviewer.create_review(
            plan,
            search_results,
            extraction_results,
            checker_results,
            normalized,
            plan_sha256=plan_sha256,
            search_sha256=search_sha256,
            extraction_sha256=extraction_sha256,
            check_sha256=check_sha256,
            normalized_sha256=normalized_sha256,
            normalized_reference=str(args.normalized.resolve()),
            report_reference=str(report_path.resolve()),
            decision=decision,
            reviewer=args.reviewer,
            reviewer_notes=args.notes,
            acknowledge_incomplete=args.acknowledge_incomplete,
        )
        report_html = render_review_html(
            results,
            plan,
            search_results,
            extraction_results,
            checker_results,
            normalized,
        )
        saved_review_path, saved_report_path = save_human_review_results(
            results,
            report_html,
            args.normalized,
            output_dir=result_directory,
        )

    if results.approved_for_import:
        next_command = (
            ".venv/bin/python src/saashome/manage.py import_franchise_research "
            f"--review {saved_review_path}"
        )
        if decision == HumanReviewDecision.APPROVED_WITH_GAPS:
            next_command += " --allow-approved-with-gaps"
    elif decision == HumanReviewDecision.PENDING:
        next_decision = (
            HumanReviewDecision.APPROVED.value
            if results.input_checker_passed and results.input_scope_complete
            else HumanReviewDecision.APPROVED_WITH_GAPS.value
        )
        next_command = (
            ".venv/bin/python -m datacollector review "
            f"--normalized {args.normalized} --decision {next_decision} "
            '--reviewer "<name>"'
        )
        if next_decision == HumanReviewDecision.APPROVED_WITH_GAPS.value:
            next_command += " --acknowledge-incomplete"
    else:
        next_command = "Return the documented gaps to Resolver/Searcher."

    summary = {
        "review_id": results.review_id,
        "normalization_id": results.normalization_id,
        "normalized_sha256": results.normalized_sha256,
        "brand": results.brand_name,
        "iteration": results.iteration,
        "decision": results.decision.value,
        "reviewer": results.reviewer,
        "approved_for_import": results.approved_for_import,
        "input_checker_passed": results.input_checker_passed,
        "input_scope_complete": results.input_scope_complete,
        "input_quality_score": results.input_quality_score,
        "coverage": results.coverage.model_dump(mode="json"),
        "warnings": results.warnings,
        "review_path": str(saved_review_path),
        "report_path": str(saved_report_path),
        "next_command": next_command,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _run_reconcile(args: argparse.Namespace) -> int:
    current, current_sha256 = load_extraction_results(args.extractions)
    if current.generated_by != "executor":
        raise ExecutorValidationError(
            "Reconcile requires an Executor-generated extraction artifact."
        )
    required_references = (
        current.plan_reference,
        current.search_reference,
        current.prior_extraction_reference,
        current.resolution_reference,
    )
    if any(reference is None for reference in required_references):
        raise ExecutorValidationError(
            "Current extraction has incomplete Executor lineage."
        )

    plan_path = args.plan or Path(current.plan_reference)
    search_path = args.sources or Path(current.search_reference)
    prior_path = args.prior_extractions or Path(
        current.prior_extraction_reference or ""
    )
    resolution_path = args.resolution or Path(current.resolution_reference or "")
    plan, plan_sha256 = load_research_plan(plan_path)
    merged_search, search_sha256 = load_search_results(search_path)
    prior, prior_sha256 = load_extraction_results(prior_path)
    resolution, resolution_sha256 = load_resolver_results(resolution_path)

    result_directory = args.output_dir or args.extractions.parent
    if (
        any(document.content_path for document in [*prior.documents, *current.documents])
        and result_directory.resolve() != args.extractions.parent.resolve()
    ):
        raise ExecutorValidationError(
            "Reconciliation output directory must match the current extraction "
            "directory while raw-document snapshots are referenced."
        )
    expected_path = result_directory / reconciled_extraction_results_filename_for(
        current.iteration
    )
    with reserve_artifact(expected_path):
        reconciled = ExecutorAgent.reconcile_extraction(
            plan,
            merged_search,
            prior,
            current,
            resolution,
            plan_sha256=plan_sha256,
            merged_search_sha256=search_sha256,
            prior_extraction_sha256=prior_sha256,
            current_extraction_sha256=current_sha256,
            resolution_sha256=resolution_sha256,
            plan_reference=str(plan_path.resolve()),
            merged_search_reference=str(search_path.resolve()),
            prior_extraction_reference=str(prior_path.resolve()),
            current_extraction_reference=str(args.extractions.resolve()),
            resolution_reference=str(resolution_path.resolve()),
        )
        reconciled_path = save_reconciled_extraction_results(
            reconciled,
            args.extractions,
            output_dir=result_directory,
        )
    _, reconciled_sha256 = load_extraction_results(reconciled_path)

    prior_claim_ids = {claim.claim_id for claim in prior.claims}
    current_claim_ids = {claim.claim_id for claim in current.claims}
    reconciled_claim_ids = {claim.claim_id for claim in reconciled.claims}
    restored_claim_ids = sorted(
        (prior_claim_ids - current_claim_ids) & reconciled_claim_ids
    )
    unknown_usage = any(
        attempt.token_usage_unknown for attempt in current.failed_attempts
    )
    summary = {
        "reconciliation_mode": "offline_deterministic",
        "iteration": reconciled.iteration,
        "current_extraction_id": current.extraction_id,
        "current_extraction_sha256": current_sha256,
        "prior_extraction_id": prior.extraction_id,
        "prior_extraction_sha256": prior_sha256,
        "reconciled_extraction_id": reconciled.extraction_id,
        "reconciled_extraction_sha256": reconciled_sha256,
        "claims_before": len(current.claims),
        "claims_after": len(reconciled.claims),
        "restored_predecessor_claims": len(restored_claim_ids),
        "restored_predecessor_claim_ids": restored_claim_ids,
        "reconciliation_api_calls": 0,
        "reconciliation_network_calls": 0,
        "reconciliation_cost_usd": "0",
        "inherited_extractor_usage_from_current_artifact": _usage_totals(
            current.agent_usage,
            failed_call_indices=[
                attempt.call_index for attempt in current.failed_attempts
            ],
            has_unknown_token_usage=unknown_usage,
        ),
        "warnings": reconciled.warnings,
        "extractions_path": str(reconciled_path),
        "next_command": (
            f".venv/bin/python -m datacollector check --extractions "
            f"{reconciled_path} --iteration {reconciled.iteration}"
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
        if args.command == "loop":
            return _run_loop(args)
        if args.command == "normalize":
            return _run_normalize(args)
        if args.command == "review":
            return _run_review(args)
        if args.command == "reconcile":
            return _run_reconcile(args)
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
        NormalizerProviderError,
        NormalizerValidationError,
        HumanReviewValidationError,
        LoopValidationError,
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
