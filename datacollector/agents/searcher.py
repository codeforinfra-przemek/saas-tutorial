"""Searcher agent: prepare a free workload or discover provider-grounded URLs."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from ..llm.protocol import ProviderSearchSource, SearcherGeneration, SearcherLLM
from ..schemas import (
    ResearchPlan,
    ResearchTask,
    SearchAction,
    SearchLimits,
    SearchResults,
    SearchSource,
    SearchSourceOrigin,
    SearchTaskResult,
    SearchTaskStatus,
    SearcherSourceDraft,
    SourceType,
)


DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "searcher_system_v1.md"
)
UNRESOLVED_QUERY_MARKER = re.compile(r"\{[^{}]+\}|\[[^\[\]]+\]|<[^<>]+>")
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
}


class SearcherValidationError(ValueError):
    """Raised before saving an invalid or misleading search artifact."""


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _canonicalize_public_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None

    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local"):
        return None
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            return None
    try:
        legacy_ipv4 = ipaddress.ip_address(socket.inet_aton(hostname))
    except OSError:
        if "." not in hostname or hostname.endswith(
            (".home", ".internal", ".lan", ".localhost")
        ):
            return None
    else:
        if not legacy_ipv4.is_global:
            return None

    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = (
        rendered_host
        if port is None or default_port
        else f"{rendered_host}:{port}"
    )
    cleaned_query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in TRACKING_QUERY_KEYS
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path or "/",
            urlencode(cleaned_query, doseq=True),
            "",
        )
    )


def _source_id(canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
    return f"source-{digest}"


def _query_key(value: str) -> str:
    return " ".join(value.split()).casefold()


def _sanitize_task(task: ResearchTask) -> tuple[ResearchTask, int]:
    queries = [
        query
        for query in task.search_queries
        if not UNRESOLVED_QUERY_MARKER.search(query)
    ]
    removed = len(task.search_queries) - len(queries)
    return task.model_copy(update={"search_queries": queries}), removed


def _select_tasks(
    plan: ResearchPlan,
    requested_task_ids: list[str],
    task_limit: int | None,
) -> list[ResearchTask]:
    requested = set(requested_task_ids)
    known_identifiers = {
        identifier
        for task in plan.tasks
        for identifier in (task.task_id, task.catalog_question_id)
    }
    unknown = requested - known_identifiers
    if unknown:
        raise SearcherValidationError(
            f"Unknown plan task identifiers: {sorted(unknown)}"
        )
    selected = [
        task
        for task in plan.tasks
        if not requested
        or task.task_id in requested
        or task.catalog_question_id in requested
    ]
    if requested and task_limit is not None and len(selected) > task_limit:
        raise SearcherValidationError(
            f"Explicit task selection matched {len(selected)} tasks but "
            f"--limit-tasks allows {task_limit}; increase the limit explicitly."
        )
    if task_limit is not None:
        selected = selected[:task_limit]
    if not selected:
        raise SearcherValidationError("No plan tasks were selected for Searcher.")
    return selected


def _seed_sources(plan: ResearchPlan, discovered_at: datetime) -> list[SearchSource]:
    urls: list[tuple[str, SourceType, str]] = []
    if plan.planner_input.known_official_website:
        urls.append(
            (
                plan.planner_input.known_official_website,
                SourceType.OFFICIAL,
                "Known official website inherited from Planner input.",
            )
        )
    for task in plan.tasks:
        for hint in task.source_hints:
            if _canonicalize_public_url(hint) is not None:
                urls.append(
                    (
                        hint,
                        SourceType.UNKNOWN,
                        f"URL source hint inherited from task {task.task_id}.",
                    )
                )
    sources: list[SearchSource] = []
    seen: set[str] = set()
    for url, source_type, seed_note in urls:
        canonical = _canonicalize_public_url(url)
        if canonical is None or canonical in seen:
            continue
        seen.add(canonical)
        sources.append(
            SearchSource(
                source_id=_source_id(canonical),
                url=url,
                canonical_url=canonical,
                title="",
                source_type=source_type,
                origin=SearchSourceOrigin.PLAN_SEED,
                provider_verified=False,
                task_ids=[],
                discovered_via_queries=[],
                relevance_note=(
                    f"{seed_note} It was not searched or validated by the free "
                    "Searcher."
                ),
                discovered_at=discovered_at,
            )
        )
    return sources


class SearcherAgent:
    def __init__(
        self,
        llm: SearcherLLM | None = None,
        *,
        prompt_path: Path | str = DEFAULT_PROMPT_PATH,
    ):
        self.llm = llm
        self.prompt_path = Path(prompt_path)

    def create_search_results(
        self,
        plan: ResearchPlan,
        *,
        plan_sha256: str,
        plan_reference: str,
        iteration: int = 1,
        requested_task_ids: list[str] | None = None,
        task_limit: int | None = 5,
        max_search_calls: int = 10,
    ) -> SearchResults:
        if iteration < 1:
            raise SearcherValidationError("Searcher iteration must be at least 1.")
        if task_limit is not None and task_limit < 1:
            raise SearcherValidationError("Searcher task limit must be at least 1.")
        if max_search_calls < 1 or max_search_calls > 100:
            raise SearcherValidationError(
                "Searcher max search calls must be between 1 and 100."
            )

        requested = _deduplicate(requested_task_ids or [])
        selected = _select_tasks(plan, requested, task_limit)
        if len(selected) > 50:
            raise SearcherValidationError(
                "One Searcher call can cover at most 50 tasks; select a smaller batch."
            )
        sanitized: list[ResearchTask] = []
        removed_queries = 0
        for task in selected:
            sanitized_task, removed = _sanitize_task(task)
            sanitized.append(sanitized_task)
            removed_queries += removed

        created_at = datetime.now(timezone.utc)
        warnings: list[str] = []
        if removed_queries:
            warnings.append(
                f"Skipped {removed_queries} plan queries containing unresolved "
                "placeholders."
            )

        if self.llm is None:
            sources = _seed_sources(plan, created_at)
            task_results = [
                SearchTaskResult(
                    task_id=task.task_id,
                    catalog_question_id=task.catalog_question_id,
                    status=SearchTaskStatus.QUERY_WORKLOAD_ONLY,
                    planned_queries=task.search_queries,
                    attempted_queries=[],
                    source_ids=[],
                    notes=(
                        "Free mode prepared the query workload only; no network "
                        "search was executed."
                    ),
                )
                for task in sanitized
            ]
            actions = []
            agent_usage = []
            warnings.append("Free Searcher performs no network search.")
            if sources:
                warnings.append(
                    "Seed URLs are unverified inputs, not discovered evidence."
                )
            else:
                warnings.append(
                    "No brand-specific URL seeds were present in the selected plan."
                )
            model = None
            generated_by = "offline"
            search_executed = False
        else:
            try:
                system_prompt = self.prompt_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise SearcherValidationError(
                    f"Cannot load Searcher prompt: {self.prompt_path}"
                ) from exc
            generation = self.llm.generate(
                plan,
                sanitized,
                system_prompt,
                iteration=iteration,
                max_search_calls=max_search_calls,
            )
            safe_actions, removed_action_urls = self._sanitize_actions(
                generation.actions
            )
            generation = SearcherGeneration(
                draft=generation.draft,
                usage=generation.usage,
                actions=safe_actions,
                provider_sources=generation.provider_sources,
            )
            sources, task_results, merge_warnings = self._merge_generation(
                sanitized,
                generation,
                created_at,
            )
            warnings.extend(generation.draft.warnings)
            warnings.extend(merge_warnings)
            if removed_action_urls:
                warnings.append(
                    f"Removed {removed_action_urls} non-public or invalid URLs "
                    "from the provider action trace."
                )
            actions = generation.actions
            agent_usage = [generation.usage]
            model = self.llm.model_name
            generated_by = "openai"
            search_executed = True

        selected_ids = [task.task_id for task in sanitized]
        unselected_ids = [
            task.task_id for task in plan.tasks if task.task_id not in set(selected_ids)
        ]
        return SearchResults(
            search_id=str(uuid4()),
            plan_run_id=plan.run_id,
            plan_sha256=plan_sha256,
            plan_reference=plan_reference,
            created_at=created_at,
            iteration=iteration,
            generated_by=generated_by,
            model=model,
            brand_name=plan.planner_input.brand_name,
            target_country=plan.planner_input.target_country,
            depth=plan.planner_input.depth,
            search_executed=search_executed,
            limits=SearchLimits(
                max_search_calls=max_search_calls,
                task_limit=task_limit,
                requested_task_ids=requested,
            ),
            selected_task_ids=selected_ids,
            unselected_task_ids=unselected_ids,
            actions=actions,
            sources=sources,
            task_results=task_results,
            warnings=_deduplicate(warnings),
            compliance_rules=plan.compliance_rules,
            agent_usage=agent_usage,
        )

    @staticmethod
    def _sanitize_actions(
        actions: list[SearchAction],
    ) -> tuple[list[SearchAction], int]:
        sanitized: list[SearchAction] = []
        removed_urls = 0
        for action in actions:
            target_url = None
            if action.target_url:
                target_url = _canonicalize_public_url(action.target_url)
                if target_url is None:
                    removed_urls += 1
            source_urls: list[str] = []
            for url in action.source_urls:
                canonical = _canonicalize_public_url(url)
                if canonical is None:
                    removed_urls += 1
                else:
                    source_urls.append(canonical)
            sanitized.append(
                action.model_copy(
                    update={
                        "target_url": target_url,
                        "source_urls": _deduplicate(source_urls),
                    }
                )
            )
        return sanitized, removed_urls

    def _merge_generation(
        self,
        tasks: list[ResearchTask],
        generation: SearcherGeneration,
        discovered_at: datetime,
    ) -> tuple[list[SearchSource], list[SearchTaskResult], list[str]]:
        selected_ids = {task.task_id for task in tasks}
        warnings: list[str] = []
        provider_by_canonical: dict[str, ProviderSearchSource] = {}
        rejected_provider_urls = 0
        for source in generation.provider_sources:
            canonical = _canonicalize_public_url(source.url)
            if canonical is None:
                rejected_provider_urls += 1
                continue
            provider_by_canonical.setdefault(canonical, source)
        if rejected_provider_urls:
            warnings.append(
                f"Rejected {rejected_provider_urls} non-public or invalid provider URLs."
            )

        draft_by_canonical: dict[str, SearcherSourceDraft] = {}
        rejected_draft_urls = 0
        unknown_source_task_ids = 0
        task_ids_by_url: defaultdict[str, set[str]] = defaultdict(set)
        for draft_source in generation.draft.sources:
            canonical = _canonicalize_public_url(draft_source.url)
            if canonical is None or canonical not in provider_by_canonical:
                rejected_draft_urls += 1
                continue
            draft_by_canonical.setdefault(canonical, draft_source)
            valid_task_ids = set(draft_source.task_ids) & selected_ids
            unknown_source_task_ids += len(set(draft_source.task_ids) - selected_ids)
            task_ids_by_url[canonical].update(valid_task_ids)

        task_drafts = {
            item.task_id: item
            for item in generation.draft.task_results
            if item.task_id in selected_ids
        }
        unknown_task_results = len(generation.draft.task_results) - len(task_drafts)
        for task_id, task_draft in task_drafts.items():
            for source_url in task_draft.source_urls:
                canonical = _canonicalize_public_url(source_url)
                if canonical is not None and canonical in provider_by_canonical:
                    task_ids_by_url[canonical].add(task_id)
                else:
                    rejected_draft_urls += 1

        if rejected_draft_urls:
            warnings.append(
                f"Rejected {rejected_draft_urls} model URLs not confirmed by "
                "provider search provenance."
            )
        if unknown_source_task_ids or unknown_task_results:
            warnings.append(
                "Removed model mappings to task IDs outside the selected plan scope."
            )

        queries_by_url: defaultdict[str, list[str]] = defaultdict(list)
        executed_queries: list[str] = []
        for action in generation.actions:
            executed_queries.extend(action.queries)
            for raw_url in action.source_urls:
                canonical = _canonicalize_public_url(raw_url)
                if canonical is not None:
                    queries_by_url[canonical].extend(action.queries)
        executed_query_keys = {_query_key(query) for query in executed_queries}

        sources: list[SearchSource] = []
        source_id_by_url: dict[str, str] = {}
        for canonical, provider_source in provider_by_canonical.items():
            draft_source = draft_by_canonical.get(canonical)
            source_id = _source_id(canonical)
            source_id_by_url[canonical] = source_id
            sources.append(
                SearchSource(
                    source_id=source_id,
                    url=provider_source.url,
                    canonical_url=canonical,
                    title=(
                        provider_source.title
                        or (draft_source.title if draft_source else "")
                    ),
                    source_type=(
                        draft_source.source_type
                        if draft_source
                        else SourceType.UNKNOWN
                    ),
                    origin=SearchSourceOrigin.OPENAI_WEB_SEARCH,
                    provider_verified=True,
                    task_ids=[
                        task.task_id
                        for task in tasks
                        if task.task_id in task_ids_by_url[canonical]
                    ],
                    discovered_via_queries=_deduplicate(queries_by_url[canonical]),
                    relevance_note=(
                        draft_source.relevance_note if draft_source else ""
                    ),
                    discovered_at=discovered_at,
                )
            )

        task_results: list[SearchTaskResult] = []
        for task in tasks:
            draft = task_drafts.get(task.task_id)
            mapped_source_ids = [
                source.source_id
                for source in sources
                if task.task_id in source.task_ids
            ]
            attempted_queries = []
            if draft is not None:
                attempted_queries = [
                    query
                    for query in draft.attempted_queries
                    if _query_key(query) in executed_query_keys
                ]
            if mapped_source_ids:
                status = SearchTaskStatus.SOURCES_FOUND
            elif (
                draft is not None
                and draft.status == SearchTaskStatus.NO_SOURCES_FOUND
                and attempted_queries
            ):
                status = SearchTaskStatus.NO_SOURCES_FOUND
            else:
                status = SearchTaskStatus.NOT_SEARCHED
            task_results.append(
                SearchTaskResult(
                    task_id=task.task_id,
                    catalog_question_id=task.catalog_question_id,
                    status=status,
                    planned_queries=task.search_queries,
                    attempted_queries=_deduplicate(attempted_queries),
                    source_ids=mapped_source_ids,
                    notes=draft.notes if draft is not None else "",
                )
            )
        return sources, task_results, warnings
