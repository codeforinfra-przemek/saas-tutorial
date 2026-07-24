"""Searcher agent: prepare a free workload or discover provider-grounded URLs."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from uuid import uuid4

from pydantic import ValidationError

from ..llm.protocol import (
    ProviderSearchSource,
    SearcherGeneration,
    SearcherLLM,
    SearcherProviderError,
)
from ..query_utils import normalize_search_queries
from ..profiles import public_automation_task_view
from ..schemas import (
    AgentIterationUsage,
    CandidateRouteDecision,
    PRIORITY_ORDER,
    ResearchPlan,
    ResearchTask,
    SearchAction,
    SearchAttemptFailure,
    SearchLimits,
    SearchQueryCoverage,
    SearchResults,
    SearchSource,
    SearchSourceOrigin,
    SearchTaskResult,
    SearchTaskStatus,
    SearcherDraft,
    SearcherSourceDraft,
    SearcherTaskDraft,
    SourceType,
)


DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "searcher_system_v4.md"
)
UNRESOLVED_QUERY_MARKER = re.compile(r"\{[^{}]+\}|\[[^\[\]]+\]|<[^<>]+>")
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
    "trk",
}
RETRY_COVERAGE_GAP_PREFIXES = (
    "planned_query_attempts:",
    "source_candidates:",
    "preferred_source_type_missing",
    "independent_candidate_domains:",
)
MULTI_LABEL_PUBLIC_SUFFIXES = {
    "ac.uk",
    "co.jp",
    "co.nz",
    "co.uk",
    "com.au",
    "com.br",
    "com.mx",
    "com.pl",
    "com.tr",
    "edu.pl",
    "gov.au",
    "gov.pl",
    "gov.uk",
    "net.au",
    "net.pl",
    "org.au",
    "org.pl",
    "org.uk",
}
PROMOTIONAL_URL_MARKERS = {
    "contest",
    "giveaway",
    "konkurs",
    "loteria",
    "plakat",
    "sweepstakes",
}
PROMOTIONAL_TASK_MARKERS = {
    "advertising",
    "campaign",
    "contest",
    "kampania",
    "konkurs",
    "marketing",
    "promotion",
    "promocj",
    "reklam",
}
GOVERNMENT_HOST_LABELS = {
    "admin",
    "court",
    "europa",
    "gouv",
    "government",
    "gov",
    "justice",
    "state",
}
CANDIDATE_ROUTER_STOP_WORDS = {
    "about",
    "brand",
    "country",
    "dane",
    "dla",
    "document",
    "franchise",
    "franczyza",
    "franczyzy",
    "from",
    "informacje",
    "official",
    "poland",
    "polska",
    "research",
    "source",
    "strona",
    "with",
    "zabka",
}
DEFAULT_MAX_CANDIDATE_ROUTES = 5
URL_TRAILING_SENTENCE_PUNCTUATION = ".,;:!?"
SITE_QUERY_DOMAIN = re.compile(
    r"(?:^|\s)site:([a-z0-9.-]+)",
    flags=re.IGNORECASE,
)


class SearcherValidationError(ValueError):
    """Raised before saving an invalid or misleading search artifact."""


def _paid_postprocessing_error(
    exc: Exception,
    usages: list[AgentIterationUsage],
) -> SearcherProviderError:
    """Preserve all known provider usage when local paid processing fails."""

    detail = type(exc).__name__
    if isinstance(exc, ValidationError):
        messages = _deduplicate(
            [
                str(error.get("msg") or "validation error")
                for error in exc.errors()
            ]
        )
        if messages:
            detail = f"ValidationError: {'; '.join(messages[:3])}"
    return SearcherProviderError(
        "Paid Searcher post-processing failed "
        f"({detail}); provider usage must be retained.",
        code="postprocessing_error",
        usages=list(usages),
    )


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _strip_url_punctuation(value: str) -> str:
    """Remove prose punctuation without changing balanced URL characters."""

    cleaned = value.strip().strip("\"'<>\u00ab\u00bb")
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = cleaned.rstrip(URL_TRAILING_SENTENCE_PUNCTUATION)
        for closing, opening in ((")", "("), ("]", "["), ("}", "{")):
            while cleaned.endswith(closing) and cleaned.count(
                closing
            ) > cleaned.count(opening):
                cleaned = cleaned[:-1]
    return cleaned


def _canonicalize_public_url(value: str) -> str | None:
    try:
        parsed = urlsplit(_strip_url_punctuation(value))
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


def _routing_tokens(value: str, *, minimum_length: int = 4) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return {
        token
        for token in re.findall(r"[a-z0-9]+", ascii_text)
        if len(token) >= minimum_length
        and token not in CANDIDATE_ROUTER_STOP_WORDS
    }


def _task_query_domains(task: ResearchTask) -> set[str]:
    return {
        match.group(1).lower().rstrip(".")
        for query in task.search_queries
        for match in SITE_QUERY_DOMAIN.finditer(query)
    }


def _authority_route_match(
    canonical_url: str,
    title: str,
    scoped_tasks: list[ResearchTask],
) -> tuple[str, list[str]] | None:
    """Route only distinctive registry, law, document, or fee URL signals."""

    parsed = urlsplit(canonical_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    candidate_terms = _routing_tokens(
        " ".join(
            [
                title,
                hostname,
                parsed.path.replace("/", " ").replace("-", " ").replace("_", " "),
            ]
        ),
        minimum_length=3,
    )

    profiles: list[tuple[list[ResearchTask], set[str]]] = []
    identity_signals = candidate_terms.intersection({"krs", "ekrs", "imsig"})
    if {"rejestru", "sadowego"}.issubset(candidate_terms):
        identity_signals.update({"rejestru", "sadowego"})
    profiles.append(
        (
            [
                task
                for task in scoped_tasks
                if "franchisor.registration_id" in task.target_fields
            ],
            identity_signals,
        )
    )

    legal_signals = candidate_terms.intersection(
        {"isap", "sejm", "uokik", "uodo"}
    )
    if hostname.endswith("eli.gov.pl"):
        legal_signals.update({"eli", "gov"})
    profiles.append(
        (
            [
                task
                for task in scoped_tasks
                if any(field.startswith("local_law.") for field in task.target_fields)
            ],
            legal_signals,
        )
    )

    document_markers = candidate_terms.intersection(
        {
            "agreement",
            "disclosure",
            "document",
            "regulamin",
            "terms",
            "umowa",
            "warunki",
        }
    )
    document_tasks = [
        task
        for task in scoped_tasks
        if any(field.startswith("documents.") for field in task.target_fields)
        and any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in _task_query_domains(task)
        )
    ]
    profiles.append((document_tasks, document_markers))

    fee_signals = candidate_terms.intersection(
        {"entry", "fee", "initial", "oplata", "wpisowe", "wstepna"}
    )
    fee_phrase_present = bool(
        {"initial", "fee"}.issubset(candidate_terms)
        or {"entry", "fee"}.issubset(candidate_terms)
        or {"oplata", "wstepna"}.issubset(candidate_terms)
        or "wpisowe" in candidate_terms
    )
    profiles.append(
        (
            [
                task
                for task in scoped_tasks
                if fee_phrase_present
                and any(field.startswith("fees.initial") for field in task.target_fields)
            ],
            fee_signals if fee_phrase_present else set(),
        )
    )

    matches = [
        (tasks[0].task_id, sorted(signals))
        for tasks, signals in profiles
        if len(tasks) == 1 and signals
    ]
    return matches[0] if len(matches) == 1 else None


def _candidate_document_quality_bonus(canonical_url: str) -> int:
    """Prefer document endpoints over news pages within one confidence tier."""

    path = urlsplit(canonical_url).path.casefold()
    bonus = 0
    if path.endswith(".pdf"):
        bonus += 8
    if any(marker in path for marker in ("/acts/", "docdetails", "download")):
        bonus += 4
    if any(
        marker in path
        for marker in ("aktualnosci", "article", "artykul", "news")
    ):
        bonus -= 8
    return bonus


def _select_candidate_route_urls(
    routes: list[CandidateRouteDecision],
    limit: int,
) -> set[str]:
    """Select high-quality routes while preserving task-level diversity."""

    ranked = sorted(
        (route for route in routes if route.decision == "routed"),
        key=lambda route: (-route.score, route.canonical_url),
    )
    selected: list[str] = []
    selected_tasks: set[str] = set()
    for route in ranked:
        if len(selected) >= limit:
            break
        assert route.routed_task_id is not None
        if route.routed_task_id in selected_tasks:
            continue
        selected.append(route.canonical_url)
        selected_tasks.add(route.routed_task_id)
    for route in ranked:
        if len(selected) >= limit:
            break
        if route.canonical_url not in selected:
            selected.append(route.canonical_url)
    return set(selected)


def _candidate_route_source_type(
    route: CandidateRouteDecision | None,
    canonical_url: str,
) -> SourceType:
    """Infer only publisher classes that are explicit in an authority URL."""

    if route is None:
        return SourceType.UNKNOWN
    hostname = (urlsplit(canonical_url).hostname or "").casefold()
    labels = set(hostname.split("."))
    if "imsig" in labels:
        return SourceType.ROUTING_LEAD
    if labels.intersection({"uokik", "uodo"}):
        return SourceType.REGULATOR
    if labels.intersection({"isap", "sejm", "eli"}):
        return SourceType.LEGAL_DOCUMENT
    return SourceType.UNKNOWN


def _attribute_action_queries(
    actions: list[SearchAction],
    tasks: list[ResearchTask],
    draft: SearcherDraft,
) -> list[SearchAction]:
    """Attach deterministic query-to-task provenance to provider actions."""

    task_order = {task.task_id: index for index, task in enumerate(tasks)}
    planned_task_ids_by_query: defaultdict[str, set[str]] = defaultdict(set)
    for task in tasks:
        for query in task.search_queries:
            planned_task_ids_by_query[_query_key(query)].add(task.task_id)
    reported_task_ids_by_query: defaultdict[str, set[str]] = defaultdict(set)
    for task_draft in draft.task_results:
        if task_draft.task_id not in task_order:
            continue
        for query in task_draft.attempted_queries:
            reported_task_ids_by_query[_query_key(query)].add(task_draft.task_id)

    attributed: list[SearchAction] = []
    for action in actions:
        scoped = set(action.scope_task_ids)
        query_task_ids: dict[str, list[str]] = {}
        for query in action.queries:
            key = _query_key(query)
            candidate_ids = planned_task_ids_by_query[key]
            if not candidate_ids:
                candidate_ids = reported_task_ids_by_query[key]
            ordered_ids = sorted(
                candidate_ids.intersection(scoped),
                key=lambda task_id: task_order[task_id],
            )
            if ordered_ids:
                query_task_ids[query] = ordered_ids
        attributed.append(
            action.model_copy(update={"query_task_ids": query_task_ids})
        )
    return attributed


def _candidate_route_decision(
    canonical_url: str,
    provider_source: ProviderSearchSource,
    action_ids: list[str],
    completed_action_by_id: dict[str, SearchAction],
    tasks: list[ResearchTask],
) -> CandidateRouteDecision:
    scoped_task_ids = _deduplicate(
        [
            task_id
            for action_id in action_ids
            for task_id in completed_action_by_id[action_id].scope_task_ids
        ]
    )
    task_by_id = {task.task_id: task for task in tasks}
    scoped_tasks = [
        task_by_id[task_id]
        for task_id in scoped_task_ids
        if task_id in task_by_id
    ]
    authority_match = _authority_route_match(
        canonical_url,
        provider_source.title,
        scoped_tasks,
    )
    quality_bonus = _candidate_document_quality_bonus(canonical_url)
    if len(scoped_task_ids) == 1:
        return CandidateRouteDecision(
            canonical_url=canonical_url,
            title=provider_source.title,
            action_ids=action_ids,
            decision="routed",
            routed_task_id=scoped_task_ids[0],
            score=max(1, 100 + quality_bonus),
            runner_up_score=0,
            matched_terms=[],
            reason_code="single_task_action",
        )

    query_actions = [
        completed_action_by_id[action_id]
        for action_id in action_ids
        if completed_action_by_id[action_id].action_type == "search"
    ]
    query_scope_complete = bool(query_actions)
    query_scoped_task_ids: list[str] = []
    for action in query_actions:
        if not action.queries or not set(action.queries).issubset(
            action.query_task_ids
        ):
            query_scope_complete = False
            continue
        query_scoped_task_ids.extend(
            task_id
            for query in action.queries
            for task_id in action.query_task_ids[query]
        )
    query_scoped_task_ids = _deduplicate(query_scoped_task_ids)
    if query_scope_complete and len(query_scoped_task_ids) == 1:
        authority_signals = (
            authority_match[1]
            if authority_match is not None
            and authority_match[0] == query_scoped_task_ids[0]
            else []
        )
        return CandidateRouteDecision(
            canonical_url=canonical_url,
            title=provider_source.title,
            action_ids=action_ids,
            decision="routed",
            routed_task_id=query_scoped_task_ids[0],
            score=max(
                1,
                90 + quality_bonus + (5 if authority_signals else 0),
            ),
            runner_up_score=0,
            matched_terms=authority_signals,
            reason_code="single_task_query_scope",
        )

    if authority_match is not None:
        task_id, signals = authority_match
        return CandidateRouteDecision(
            canonical_url=canonical_url,
            title=provider_source.title,
            action_ids=action_ids,
            decision="routed",
            routed_task_id=task_id,
            score=max(1, 80 + quality_bonus + len(signals)),
            runner_up_score=0,
            matched_terms=signals,
            reason_code="authority_path_match",
        )
    task_terms: dict[str, set[str]] = {}
    document_frequency: defaultdict[str, int] = defaultdict(int)
    for task in scoped_tasks:
        terms = _routing_tokens(
            " ".join(
                [
                    task.title,
                    task.question,
                    task.acceptance_criteria,
                    *task.target_fields,
                    *task.search_queries,
                ]
            )
        )
        task_terms[task.task_id] = terms
        for term in terms:
            document_frequency[term] += 1

    parsed = urlsplit(canonical_url)
    candidate_terms = _routing_tokens(
        " ".join(
            [
                provider_source.title,
                parsed.hostname or "",
                parsed.path.replace("/", " ").replace("-", " ").replace("_", " "),
            ]
        )
    )
    scores: list[tuple[int, str, list[str]]] = []
    for task in scoped_tasks:
        matched = sorted(candidate_terms.intersection(task_terms[task.task_id]))
        score = sum(
            4 if document_frequency[term] == 1 else 1
            for term in matched
            if document_frequency[term] < len(scoped_tasks)
        )
        scores.append((score, task.task_id, matched))
    scores.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_task_id, best_terms = scores[0] if scores else (0, "", [])
    runner_up_score = scores[1][0] if len(scores) > 1 else 0
    if best_score >= 8 and len(best_terms) >= 2 and best_score - runner_up_score >= 4:
        return CandidateRouteDecision(
            canonical_url=canonical_url,
            title=provider_source.title,
            action_ids=action_ids,
            decision="routed",
            routed_task_id=best_task_id,
            score=max(1, best_score + quality_bonus),
            runner_up_score=runner_up_score,
            matched_terms=best_terms,
            reason_code="unique_lexical_match",
        )
    return CandidateRouteDecision(
        canonical_url=canonical_url,
        title=provider_source.title,
        action_ids=action_ids,
        decision="unassigned",
        routed_task_id=None,
        score=best_score,
        runner_up_score=runner_up_score,
        matched_terms=best_terms,
        reason_code=(
            "ambiguous_scope"
            if best_score and best_score - runner_up_score < 4
            else "insufficient_lexical_evidence"
        ),
    )


def _candidate_domain(value: str) -> str:
    """Return a conservative registrable-domain approximation."""

    hostname = (urlsplit(value).hostname or "").lower().rstrip(".")
    labels = hostname.removeprefix("www.").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    suffix = ".".join(labels[-2:])
    label_count = 3 if suffix in MULTI_LABEL_PUBLIC_SUFFIXES else 2
    return ".".join(labels[-label_count:])


def _sanitize_task(task: ResearchTask) -> tuple[ResearchTask, int, int]:
    queries = [
        query
        for query in task.search_queries
        if not UNRESOLVED_QUERY_MARKER.search(query)
    ]
    removed = len(task.search_queries) - len(queries)
    normalized_queries, normalized = normalize_search_queries(queries)
    return (
        task.model_copy(update={"search_queries": normalized_queries}),
        removed,
        normalized,
    )


def _is_unrelated_promotional_source(
    url: str,
    mapped_tasks: list[ResearchTask],
) -> bool:
    parsed = urlsplit(url)
    url_words = set(re.findall(r"[a-z0-9ąćęłńóśźż]+", parsed.path.casefold()))
    if not url_words.intersection(PROMOTIONAL_URL_MARKERS):
        return False
    task_text = " ".join(
        value
        for task in mapped_tasks
        for value in (
            task.title,
            task.question,
            task.acceptance_criteria,
            *task.target_fields,
        )
    )
    task_words = set(re.findall(r"[a-z0-9ąćęłńóśźż]+", task_text.casefold()))
    return not task_words.intersection(PROMOTIONAL_TASK_MARKERS)


def _refine_source_type(
    source_type: SourceType,
    relevance_note: str,
    url: str = "",
) -> SourceType:
    note = relevance_note.casefold()
    hostname_labels = set((urlsplit(url).hostname or "").casefold().split("."))
    if source_type == SourceType.REGISTRY:
        is_explicit_routing_lead = any(
            marker in note
            for marker in (
                "aggregat",
                "routing lead",
                "third-party",
                "third party",
            )
        )
        is_probably_official_host = bool(
            hostname_labels.intersection(GOVERNMENT_HOST_LABELS)
        )
        if is_explicit_routing_lead or (url and not is_probably_official_host):
            return SourceType.ROUTING_LEAD
    proposed_law_markers = (
        "draft law",
        "legislative project",
        "legislative-project",
        "projekt ustawy",
        "proposed law",
    )
    url_path = urlsplit(url).path.casefold()
    if source_type in {SourceType.GOVERNMENT, SourceType.LEGAL_DOCUMENT} and (
        any(marker in note for marker in proposed_law_markers)
        or any(
            marker in url_path
            for marker in ("draft-bill", "projekt-ustawy", "proposed-law")
        )
    ):
        return SourceType.LEGISLATIVE_PROJECT
    return source_type


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


def _seed_sources(
    plan: ResearchPlan,
    discovered_at: datetime,
    *,
    tasks: list[ResearchTask] | None = None,
    official_first: bool = False,
) -> list[SearchSource]:
    urls: list[tuple[str, SourceType, str]] = []
    if plan.planner_input.known_official_website:
        website = plan.planner_input.known_official_website
        urls.append((website, SourceType.UNKNOWN, "Unverified website seed inherited from Planner input."))
        if official_first:
            parsed = urlsplit(website)
            hostname = (parsed.hostname or "").casefold()
            hosts = [hostname]
            if hostname.startswith("www."):
                hosts.append(hostname[4:])
            elif hostname:
                hosts.append(f"www.{hostname}")
            roots = [
                urlunsplit((parsed.scheme, host, "/", "", ""))
                for host in dict.fromkeys(hosts)
            ]
            for root in roots:
                for path in (
                    "franczyza/",
                    "franchise/",
                    "oferta-franczyzowa/",
                    "wspolpraca/",
                    "o-nas/",
                    "kontakt/",
                ):
                    urls.append(
                        (
                            urljoin(root, path),
                            SourceType.UNKNOWN,
                            "Deterministic official-first host/path candidate; "
                            "reachability and ownership are not yet verified.",
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
    task_ids = [task.task_id for task in (tasks or plan.tasks)]
    seen: set[str] = set()
    for url, source_type, seed_note in urls:
        canonical = _canonicalize_public_url(url)
        if canonical is None or canonical in seen:
            continue
        seen.add(canonical)
        sources.append(
            SearchSource(
                source_id=_source_id(canonical),
                url=canonical,
                canonical_url=canonical,
                title="",
                source_type=source_type,
                origin=SearchSourceOrigin.PLAN_SEED,
                provider_observed=False,
                task_ids=task_ids,
                observed_in_action_ids=[],
                discovered_via_queries=[],
                relevance_note=(
                    f"{seed_note} It is a retrieval candidate, not evidence; "
                    "Extractor and Human Review must validate it."
                ),
                discovered_at=discovered_at,
            )
        )
    return sources


def _combine_generations(
    generations: list[SearcherGeneration],
) -> SearcherGeneration:
    """Combine successful provider calls before deterministic validation."""

    if not generations:
        raise SearcherValidationError("Searcher produced no successful generations.")

    warnings: list[str] = []
    sources_by_url: dict[str, SearcherSourceDraft] = {}
    task_drafts: dict[str, SearcherTaskDraft] = {}
    actions: list[SearchAction] = []
    provider_sources_by_url: dict[str, ProviderSearchSource] = {}

    for generation in generations:
        warnings.extend(generation.draft.warnings)
        actions.extend(generation.actions)
        for provider_source in generation.provider_sources:
            canonical = _canonicalize_public_url(provider_source.url)
            key = canonical or provider_source.url
            existing_provider = provider_sources_by_url.get(key)
            if existing_provider is None or len(provider_source.title) > len(
                existing_provider.title
            ):
                provider_sources_by_url[key] = ProviderSearchSource(
                    url=canonical or provider_source.url,
                    title=provider_source.title,
                )

        for draft_source in generation.draft.sources:
            canonical = _canonicalize_public_url(draft_source.url)
            key = canonical or draft_source.url
            existing = sources_by_url.get(key)
            if existing is None:
                sources_by_url[key] = draft_source
                continue
            sources_by_url[key] = existing.model_copy(
                update={
                    "title": existing.title or draft_source.title,
                    "source_type": (
                        draft_source.source_type
                        if existing.source_type == SourceType.UNKNOWN
                        else existing.source_type
                    ),
                    "task_ids": _deduplicate(
                        [*existing.task_ids, *draft_source.task_ids]
                    ),
                    "relevance_note": (
                        existing.relevance_note or draft_source.relevance_note
                    ),
                }
            )

        for draft in generation.draft.task_results:
            existing = task_drafts.get(draft.task_id)
            if existing is None:
                task_drafts[draft.task_id] = draft
                continue
            notes = _deduplicate([existing.notes, draft.notes])
            task_drafts[draft.task_id] = existing.model_copy(
                update={
                    "status": draft.status,
                    "attempted_queries": _deduplicate(
                        [*existing.attempted_queries, *draft.attempted_queries]
                    ),
                    "source_urls": _deduplicate(
                        [*existing.source_urls, *draft.source_urls]
                    ),
                    "unresolved_targets": draft.unresolved_targets,
                    "notes": "; ".join(notes)[:1000],
                }
            )

    combined_draft = SearcherDraft.model_construct(
        warnings=_deduplicate(warnings),
        sources=list(sources_by_url.values()),
        task_results=list(task_drafts.values()),
    )
    return SearcherGeneration(
        draft=combined_draft,
        usage=generations[0].usage,
        actions=actions,
        provider_sources=list(provider_sources_by_url.values()),
    )


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
        min_queries_per_task: int = 1,
        max_retry_tasks: int = 0,
        retry_search_calls: int = 1,
        max_candidate_routes: int = DEFAULT_MAX_CANDIDATE_ROUTES,
        query_overrides: dict[str, list[str]] | None = None,
        official_first: bool = False,
    ) -> SearchResults:
        if iteration < 1:
            raise SearcherValidationError("Searcher iteration must be at least 1.")
        if task_limit is not None and task_limit < 1:
            raise SearcherValidationError("Searcher task limit must be at least 1.")
        if max_search_calls < 1 or max_search_calls > 100:
            raise SearcherValidationError(
                "Searcher max search calls must be between 1 and 100."
            )
        if min_queries_per_task < 1 or min_queries_per_task > 20:
            raise SearcherValidationError(
                "Searcher minimum queries per task must be between 1 and 20."
            )
        if max_retry_tasks < 0 or max_retry_tasks > 50:
            raise SearcherValidationError(
                "Searcher max retry tasks must be between 0 and 50."
            )
        if retry_search_calls < 1 or retry_search_calls > 10:
            raise SearcherValidationError(
                "Searcher retry search calls must be between 1 and 10."
            )
        if max_candidate_routes < 0 or max_candidate_routes > 100:
            raise SearcherValidationError(
                "Searcher max candidate routes must be between 0 and 100."
            )
        if max_retry_tasks and max_search_calls < 2:
            raise SearcherValidationError(
                "Quality retry requires at least two global search calls."
            )
        if self.llm is None and max_retry_tasks:
            raise SearcherValidationError(
                "Offline Searcher cannot run paid quality retries."
            )

        requested = _deduplicate(requested_task_ids or [])
        selected = _select_tasks(plan, requested, task_limit)
        blocked_task_ids: list[str] = []
        public_task_views: list[ResearchTask] = []
        excluded_profile_fields = 0
        for task in selected:
            public_view = public_automation_task_view(plan, task)
            if public_view is None:
                blocked_task_ids.append(task.task_id)
            else:
                public_task_views.append(public_view)
                excluded_profile_fields += len(task.target_fields) - len(
                    public_view.target_fields
                )
        if blocked_task_ids and requested:
            raise SearcherValidationError(
                "Profile policy forbids public-web automation for requested "
                f"task(s): {blocked_task_ids}. Route them to Human Review or "
                "local audit instead."
            )
        selected = public_task_views
        if not selected:
            raise SearcherValidationError(
                "No selected task contains fields permitted for public-web "
                "automation by the research profile."
            )
        overrides = {
            task_id: _deduplicate(queries)
            for task_id, queries in (query_overrides or {}).items()
        }
        selected_ids_for_overrides = {task.task_id for task in selected}
        unknown_override_tasks = set(overrides) - selected_ids_for_overrides
        if unknown_override_tasks:
            raise SearcherValidationError(
                "Query overrides reference unselected task IDs: "
                f"{sorted(unknown_override_tasks)}"
            )
        if any(
            not queries
            or any(
                not query.strip() or len(query) > 500 or "\x00" in query
                for query in queries
            )
            for queries in overrides.values()
        ):
            raise SearcherValidationError(
                "Query overrides must contain bounded, non-empty plain text."
            )
        if len(selected) > 50:
            raise SearcherValidationError(
                "One Searcher call can cover at most 50 tasks; select a smaller batch."
            )
        sanitized: list[ResearchTask] = []
        removed_queries = 0
        normalized_queries = 0
        for task in selected:
            sanitized_task, removed, normalized = _sanitize_task(task)
            if task.task_id in overrides:
                normalized_override, override_normalized = normalize_search_queries(
                    overrides[task.task_id]
                )
                overrides[task.task_id] = normalized_override
                sanitized_task = sanitized_task.model_copy(
                    update={"search_queries": normalized_override}
                )
                normalized += override_normalized
            sanitized.append(sanitized_task)
            removed_queries += removed
            normalized_queries += normalized
        tasks_without_queries = [
            task.task_id for task in sanitized if not task.search_queries
        ]
        if self.llm is not None and tasks_without_queries:
            raise SearcherValidationError(
                "Paid Searcher cannot run tasks without executable Planner "
                f"queries: {tasks_without_queries}"
            )

        created_at = datetime.now(timezone.utc)
        warnings: list[str] = []
        failed_attempts: list[SearchAttemptFailure] = []
        if blocked_task_ids:
            warnings.append(
                "Skipped "
                f"{len(blocked_task_ids)} task(s) with no profile fields "
                "permitted for public-web automation."
            )
        if excluded_profile_fields:
            warnings.append(
                "Excluded "
                f"{excluded_profile_fields} private, manual, confidential, or "
                "system-derived profile field(s) from the Searcher model scope."
            )
        if removed_queries:
            warnings.append(
                f"Skipped {removed_queries} plan queries containing unresolved "
                "placeholders."
            )
        if normalized_queries:
            warnings.append(
                f"Normalized {normalized_queries} plan queries containing "
                "duplicate adjacent terms or duplicate query variants."
            )

        if self.llm is None:
            sources = _seed_sources(
                plan,
                created_at,
                tasks=sanitized,
                official_first=official_first,
            )
            task_results = []
            for task in sanitized:
                seed_source_ids = [
                    source.source_id
                    for source in sources
                    if task.task_id in source.task_ids
                ]
                task_results.append(SearchTaskResult(
                    task_id=task.task_id,
                    catalog_question_id=task.catalog_question_id,
                    status=(
                        SearchTaskStatus.PARTIAL
                        if seed_source_ids
                        else SearchTaskStatus.QUERY_WORKLOAD_ONLY
                    ),
                    planned_queries=task.search_queries,
                    attempted_queries=[],
                    planned_queries_attempted=[],
                    derived_queries_attempted=[],
                    query_coverage=(
                        SearchQueryCoverage.NONE
                        if seed_source_ids
                        else SearchQueryCoverage.WORKLOAD_ONLY
                    ),
                    minimum_query_attempts=min(
                        min_queries_per_task,
                        len(task.search_queries),
                    ),
                    minimum_sources=task.min_sources,
                    action_ids=[],
                    source_ids=seed_source_ids,
                    coverage_gaps=(
                        ["unverified_seed_requires_retrieval_and_validation"]
                        if seed_source_ids
                        else []
                    ),
                    unresolved_targets=[],
                    notes=(
                        "Free mode materialized unverified seed candidates and the "
                        "query workload; no network search was executed."
                    ),
                ))
            actions = []
            candidate_routes: list[CandidateRouteDecision] = []
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
            if official_first and plan.planner_input.known_official_website:
                system_prompt += (
                    "\n\nOFFICIAL-FIRST L1 MODE: The supplied website is an unverified "
                    "seed that is fetched locally outside your tool budget. Use web "
                    "search only for missing field-specific pages, registries, broad "
                    "directory discovery, and independent confirmation. Never call "
                    "the seed official merely because it was supplied. Prefer a few "
                    "precise queries over repeating homepage discovery."
                )
            reserved_retry_calls = min(
                max_retry_tasks * retry_search_calls,
                max_search_calls - 1,
            )
            initial_search_calls = max_search_calls - reserved_retry_calls
            generation = self.llm.generate(
                plan,
                sanitized,
                system_prompt,
                iteration=iteration,
                call_index=1,
                max_search_calls=initial_search_calls,
                min_queries_per_task=min_queries_per_task,
            )
            agent_usage = [generation.usage]
            try:
                safe_actions, removed_action_urls = self._sanitize_actions(
                    generation.actions
                )
                safe_actions = _attribute_action_queries(
                    safe_actions,
                    sanitized,
                    generation.draft,
                )
                generation = SearcherGeneration(
                    draft=generation.draft,
                    usage=generation.usage,
                    actions=safe_actions,
                    provider_sources=generation.provider_sources,
                )
                generations = [generation]
                removed_action_url_count = removed_action_urls
                initial_sources, initial_task_results, _, _ = self._merge_generation(
                    sanitized,
                    generation,
                    created_at,
                    min_queries_per_task=min_queries_per_task,
                    max_candidate_routes=max_candidate_routes,
                )
            except Exception as exc:
                raise _paid_postprocessing_error(exc, agent_usage) from None
            del initial_sources

            if max_retry_tasks:
                task_by_id = {task.task_id: task for task in sanitized}
                plan_order = {
                    task.task_id: index for index, task in enumerate(sanitized)
                }
                retryable_ids = [
                    result.task_id
                    for result in initial_task_results
                    if result.status
                    in {
                        SearchTaskStatus.PARTIAL,
                        SearchTaskStatus.NO_SOURCES_FOUND,
                        SearchTaskStatus.NOT_SEARCHED,
                    }
                    and (
                        result.status != SearchTaskStatus.PARTIAL
                        or any(
                            gap.startswith(RETRY_COVERAGE_GAP_PREFIXES)
                            for gap in result.coverage_gaps
                        )
                    )
                ]
                retryable_ids.sort(
                    key=lambda task_id: (
                        -PRIORITY_ORDER[task_by_id[task_id].priority],
                        plan_order[task_id],
                    )
                )
                for task_id in retryable_ids[:max_retry_tasks]:
                    used_actions = sum(
                        len(item.actions) for item in generations
                    )
                    remaining_calls = max_search_calls - used_actions
                    if remaining_calls < 1:
                        warnings.append(
                            "Quality retry stopped because the global tool-call "
                            "limit was exhausted."
                        )
                        break
                    call_index = len(generations) + len(failed_attempts) + 1
                    retry_task = task_by_id[task_id]
                    try:
                        retry_generation = self.llm.generate(
                            plan,
                            [retry_task],
                            system_prompt,
                            iteration=iteration,
                            call_index=call_index,
                            max_search_calls=min(
                                retry_search_calls,
                                remaining_calls,
                            ),
                            min_queries_per_task=min_queries_per_task,
                        )
                    except SearcherProviderError as exc:
                        usage_recorded = exc.usage is not None
                        if usage_recorded:
                            agent_usage.append(exc.usage)
                        failed_attempts.append(
                            SearchAttemptFailure(
                                call_index=call_index,
                                scope_task_ids=[task_id],
                                error_code=exc.code,
                                usage_recorded=usage_recorded,
                                observed_tool_calls=exc.observed_tool_calls,
                                tool_usage=exc.tool_usage,
                                token_usage_unknown=not usage_recorded,
                            )
                        )
                        warnings.append(
                            f"Quality retry call {call_index} for {task_id} "
                            f"failed with {exc.code}; retained earlier results."
                        )
                        break
                    agent_usage.append(retry_generation.usage)
                    try:
                        safe_retry_actions, removed_retry_urls = (
                            self._sanitize_actions(retry_generation.actions)
                        )
                        safe_retry_actions = _attribute_action_queries(
                            safe_retry_actions,
                            [retry_task],
                            retry_generation.draft,
                        )
                        removed_action_url_count += removed_retry_urls
                        generations.append(
                            SearcherGeneration(
                                draft=retry_generation.draft,
                                usage=retry_generation.usage,
                                actions=safe_retry_actions,
                                provider_sources=retry_generation.provider_sources,
                            )
                        )
                    except Exception as exc:
                        raise _paid_postprocessing_error(exc, agent_usage) from None

            try:
                combined_generation = _combine_generations(generations)
                sources, task_results, merge_warnings, candidate_routes = self._merge_generation(
                    sanitized,
                    combined_generation,
                    created_at,
                    min_queries_per_task=min_queries_per_task,
                    max_candidate_routes=max_candidate_routes,
                )
            except Exception as exc:
                raise _paid_postprocessing_error(exc, agent_usage) from None
            warnings.extend(combined_generation.draft.warnings)
            warnings.extend(merge_warnings)
            if removed_action_url_count:
                warnings.append(
                    f"Removed {removed_action_url_count} non-public or invalid URLs "
                    "from the provider action trace."
                )
            actions = combined_generation.actions
            model = self.llm.model_name
            generated_by = "openai"
            search_executed = True

            # Paid web search must not make a user-provided seed disappear.  It
            # remains explicitly unverified and is never promoted to OFFICIAL
            # merely because it was present in the directory database.
            seed_sources = _seed_sources(
                plan,
                created_at,
                tasks=sanitized,
                official_first=official_first,
            )
            source_index = {
                source.canonical_url: index for index, source in enumerate(sources)
            }
            for seed_source in seed_sources:
                existing_index = source_index.get(seed_source.canonical_url)
                if existing_index is None:
                    source_index[seed_source.canonical_url] = len(sources)
                    sources.append(seed_source)
                # If the provider independently returned the same URL, retain
                # its narrower action-backed task attribution. Expanding a
                # provider source to every seed task would invent provenance;
                # adding the seed's shared source ID without expanding it made
                # task/source mappings asymmetric.
            seed_urls = {source.canonical_url for source in seed_sources}
            seed_source_ids_by_task = {
                task.task_id: [
                    source.source_id
                    for source in sources
                    if source.canonical_url in seed_urls
                    and task.task_id in source.task_ids
                ]
                for task in sanitized
            }
            routed_task_results = []
            for result in task_results:
                seed_source_ids = seed_source_ids_by_task[result.task_id]
                update = {
                    "source_ids": _deduplicate(
                        [*result.source_ids, *seed_source_ids]
                    )
                }
                if seed_source_ids and result.status in {
                    SearchTaskStatus.NOT_SEARCHED,
                    SearchTaskStatus.NO_SOURCES_FOUND,
                    SearchTaskStatus.QUERY_WORKLOAD_ONLY,
                }:
                    update["status"] = SearchTaskStatus.PARTIAL
                    update["coverage_gaps"] = _deduplicate(
                        [
                            *result.coverage_gaps,
                            "unverified_seed_requires_retrieval_and_validation",
                        ]
                    )
                routed_task_results.append(result.model_copy(update=update))
            task_results = routed_task_results
            if seed_sources:
                warnings.append(
                    f"Added {len(seed_sources)} unverified official-first retrieval candidate(s); "
                    "none is treated as official evidence before validation."
                )

        selected_ids = [task.task_id for task in sanitized]
        unselected_ids = [
            task.task_id for task in plan.tasks if task.task_id not in set(selected_ids)
        ]
        provider_tool_call_overrun = max(
            0,
            len(actions)
            + sum(attempt.observed_tool_calls for attempt in failed_attempts)
            - max_search_calls,
        )
        if provider_tool_call_overrun:
            warnings.append(
                "Provider returned "
                f"{provider_tool_call_overrun} tool call(s) beyond the requested "
                f"max_search_calls={max_search_calls}; retained all billed actions "
                "and exact provenance instead of discarding the paid response."
            )
        try:
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
                    min_queries_per_task=min_queries_per_task,
                    max_retry_tasks=max_retry_tasks,
                    retry_search_calls=retry_search_calls,
                    max_candidate_routes=max_candidate_routes,
                    query_overrides=overrides,
                    official_first=official_first,
                ),
                selected_task_ids=selected_ids,
                unselected_task_ids=unselected_ids,
                actions=actions,
                provider_tool_call_overrun=provider_tool_call_overrun,
                candidate_routes=candidate_routes,
                sources=sources,
                task_results=task_results,
                warnings=_deduplicate(warnings),
                compliance_rules=plan.compliance_rules,
                agent_usage=agent_usage,
                failed_attempts=failed_attempts,
            )
        except Exception as exc:
            if self.llm is not None and agent_usage:
                raise _paid_postprocessing_error(exc, agent_usage) from None
            raise

    @staticmethod
    def _sanitize_actions(
        actions: list[SearchAction],
    ) -> tuple[list[SearchAction], int]:
        sanitized: list[SearchAction] = []
        removed_urls = 0
        for action_index, action in enumerate(actions, 1):
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
                        "action_id": action.action_id
                        or (
                            f"call-{action.call_index:03d}-"
                            f"action-{action_index:03d}"
                        ),
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
        *,
        min_queries_per_task: int,
        max_candidate_routes: int,
    ) -> tuple[
        list[SearchSource],
        list[SearchTaskResult],
        list[str],
        list[CandidateRouteDecision],
    ]:
        selected_ids = {task.task_id for task in tasks}
        warnings: list[str] = []
        provider_by_canonical: dict[str, ProviderSearchSource] = {}
        rejected_provider_urls = 0
        for source in generation.provider_sources:
            canonical = _canonicalize_public_url(source.url)
            if canonical is None:
                rejected_provider_urls += 1
                continue
            existing = provider_by_canonical.get(canonical)
            if existing is None or len(source.title) > len(existing.title):
                provider_by_canonical[canonical] = ProviderSearchSource(
                    url=canonical,
                    title=source.title,
                )
        if rejected_provider_urls:
            warnings.append(
                f"Rejected {rejected_provider_urls} non-public or invalid "
                "provider URLs."
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

        completed_actions = [
            action for action in generation.actions if action.status == "completed"
        ]
        completed_action_by_id = {
            action.action_id: action
            for action in completed_actions
            if action.action_id is not None
        }
        action_ids_by_url: defaultdict[str, list[str]] = defaultdict(list)
        for action in completed_actions:
            action_urls = list(action.source_urls)
            if action.target_url:
                action_urls.append(action.target_url)
            for raw_url in action_urls:
                canonical = _canonicalize_public_url(raw_url)
                if canonical is None or action.action_id is None:
                    continue
                action_ids_by_url[canonical].append(action.action_id)

        task_specific_queries: dict[str, list[str]] = {}
        task_action_ids: dict[str, list[str]] = {}
        attributed_query_keys: set[str] = set()
        reported_task_ids_by_query: defaultdict[str, set[str]] = defaultdict(set)
        for task_id, task_draft in task_drafts.items():
            for query in task_draft.attempted_queries:
                reported_task_ids_by_query[_query_key(query)].add(task_id)
        for task in tasks:
            planned_queries = set(task.search_queries)
            attempted: list[str] = []
            relevant_action_ids: list[str] = []
            for action in completed_actions:
                action_matches_task = False
                single_task_scope = action.scope_task_ids == [task.task_id]
                for query in action.queries:
                    uniquely_reported_for_task = reported_task_ids_by_query[
                        _query_key(query)
                    ] == {task.task_id}
                    uniquely_attributed_to_task = action.query_task_ids.get(
                        query,
                        [],
                    ) == [task.task_id]
                    if task.task_id in action.scope_task_ids and (
                        query in planned_queries
                        or single_task_scope
                        or uniquely_reported_for_task
                        or uniquely_attributed_to_task
                    ):
                        attempted.append(query)
                        attributed_query_keys.add(_query_key(query))
                        action_matches_task = True
                if action_matches_task and action.action_id is not None:
                    relevant_action_ids.append(action.action_id)
            task_specific_queries[task.task_id] = _deduplicate(attempted)
            task_action_ids[task.task_id] = _deduplicate(relevant_action_ids)

        executed_queries = _deduplicate(
            [query for action in completed_actions for query in action.queries]
        )
        unattributed_queries = [
            query
            for query in executed_queries
            if _query_key(query) not in attributed_query_keys
        ]
        if unattributed_queries:
            warnings.append(
                f"Kept {len(unattributed_queries)} executed queries only in the "
                "action trace because a multi-task batch did not provide "
                "deterministic task attribution."
            )
        multi_query_search_actions = sum(
            action.action_type == "search" and len(action.queries) > 1
            for action in completed_actions
        )
        if multi_query_search_actions:
            warnings.append(
                f"Provider returned {multi_query_search_actions} multi-query "
                "search action(s); URL-to-query provenance remains action-level "
                "for those results."
            )

        sources: list[SearchSource] = []
        unassigned_provider_sources = 0
        actionless_provider_sources = 0
        promotional_provider_sources = 0
        refined_source_types = 0
        task_by_id = {task.task_id: task for task in tasks}
        routes_by_canonical: dict[str, CandidateRouteDecision] = {}
        for canonical, provider_source in provider_by_canonical.items():
            candidate_task_ids = [
                task.task_id
                for task in tasks
                if task.task_id in task_ids_by_url[canonical]
            ]
            url_action_ids = _deduplicate(action_ids_by_url[canonical])
            mapped_task_ids = [
                task_id
                for task_id in candidate_task_ids
                if any(
                    task_id in completed_action_by_id[action_id].scope_task_ids
                    for action_id in url_action_ids
                )
            ]
            if mapped_task_ids or not url_action_ids:
                continue
            route = _candidate_route_decision(
                canonical,
                provider_source,
                url_action_ids,
                completed_action_by_id,
                tasks,
            )
            if route.decision == "routed":
                assert route.routed_task_id is not None
                route_tasks = [task_by_id[route.routed_task_id]]
                if _is_unrelated_promotional_source(canonical, route_tasks):
                    promotional_provider_sources += 1
                    route = route.model_copy(
                        update={
                            "decision": "excluded",
                            "routed_task_id": None,
                            "reason_code": "promotional_source_excluded",
                        }
                    )
            routes_by_canonical[canonical] = route

        allowed_routed_urls = _select_candidate_route_urls(
            list(routes_by_canonical.values()),
            max_candidate_routes,
        )
        for canonical, route in list(routes_by_canonical.items()):
            if (
                route.decision == "routed"
                and canonical not in allowed_routed_urls
            ):
                routes_by_canonical[canonical] = route.model_copy(
                    update={
                        "decision": "limit_reached",
                        "routed_task_id": None,
                        "reason_code": "route_limit_reached",
                    }
                )
        candidate_routes = list(routes_by_canonical.values())
        routed_candidate_count = len(allowed_routed_urls)

        for canonical, provider_source in provider_by_canonical.items():
            candidate_task_ids = [
                task.task_id
                for task in tasks
                if task.task_id in task_ids_by_url[canonical]
            ]
            url_action_ids = _deduplicate(action_ids_by_url[canonical])
            mapped_task_ids = [
                task_id
                for task_id in candidate_task_ids
                if any(
                    task_id in completed_action_by_id[action_id].scope_task_ids
                    for action_id in url_action_ids
                )
            ]
            if not mapped_task_ids:
                if not url_action_ids:
                    actionless_provider_sources += 1
                    continue
                route = routes_by_canonical[canonical]
                if route.decision == "routed":
                    assert route.routed_task_id is not None
                    mapped_task_ids = [route.routed_task_id]
                if not mapped_task_ids:
                    unassigned_provider_sources += 1
                    continue
            observed_action_ids = [
                action_id
                for action_id in url_action_ids
                if set(completed_action_by_id[action_id].scope_task_ids).intersection(
                    mapped_task_ids
                )
            ]
            if not observed_action_ids:
                actionless_provider_sources += 1
                continue
            draft_source = draft_by_canonical.get(canonical)
            mapped_tasks = [task_by_id[task_id] for task_id in mapped_task_ids]
            if _is_unrelated_promotional_source(canonical, mapped_tasks):
                promotional_provider_sources += 1
                continue
            relevance_note = (
                draft_source.relevance_note if draft_source else ""
            )
            matching_route = next(
                (
                    route
                    for route in candidate_routes
                    if route.canonical_url == canonical
                    and route.decision == "routed"
                ),
                None,
            )
            if matching_route is not None:
                relevance_note = (
                    "Deterministic Candidate Router assigned this provider-observed "
                    f"URL to {matching_route.routed_task_id} using "
                    f"{matching_route.reason_code}."
                )
            proposed_source_type = (
                draft_source.source_type
                if draft_source
                else _candidate_route_source_type(
                    matching_route,
                    canonical,
                )
            )
            source_type = _refine_source_type(
                proposed_source_type,
                relevance_note,
                canonical,
            )
            if source_type != proposed_source_type:
                refined_source_types += 1
            source_id = _source_id(canonical)
            sources.append(
                SearchSource(
                    source_id=source_id,
                    url=canonical,
                    canonical_url=canonical,
                    title=(
                        provider_source.title
                        or (draft_source.title if draft_source else "")
                    ),
                    source_type=source_type,
                    origin=SearchSourceOrigin.OPENAI_WEB_SEARCH,
                    provider_observed=True,
                    task_ids=mapped_task_ids,
                    observed_in_action_ids=observed_action_ids,
                    discovered_via_queries=_deduplicate(
                        [
                            completed_action_by_id[action_id].queries[0]
                            for action_id in observed_action_ids
                            if len(completed_action_by_id[action_id].queries) == 1
                            and (
                                set(
                                    completed_action_by_id[
                                        action_id
                                    ].query_task_ids.get(
                                        completed_action_by_id[action_id].queries[0],
                                        [],
                                    )
                                ).intersection(mapped_task_ids)
                                or (
                                    not completed_action_by_id[
                                        action_id
                                    ].query_task_ids
                                    and len(
                                        completed_action_by_id[
                                            action_id
                                        ].scope_task_ids
                                    )
                                    == 1
                                )
                            )
                        ]
                    ),
                    relevance_note=relevance_note,
                    discovered_at=discovered_at,
                )
            )
        if unassigned_provider_sources:
            warnings.append(
                f"Left {unassigned_provider_sources} unassigned provider URL "
                "candidates in the action trace instead of forwarding them to "
                "Extractor."
            )
        if routed_candidate_count:
            warnings.append(
                f"Candidate Router deterministically assigned {routed_candidate_count} "
                "previously unassigned provider URL candidate(s)."
            )
        if actionless_provider_sources:
            warnings.append(
                f"Excluded {actionless_provider_sources} mapped provider URLs "
                "without action-level provenance."
            )
        if promotional_provider_sources:
            warnings.append(
                f"Excluded {promotional_provider_sources} promotional URL "
                "candidates that did not match a selected task target."
            )
        if refined_source_types:
            warnings.append(
                f"Reclassified {refined_source_types} source candidates as "
                "routing leads or legislative projects based on their own "
                "routing metadata and source domains."
            )

        task_results: list[SearchTaskResult] = []
        for task in tasks:
            draft = task_drafts.get(task.task_id)
            mapped_source_ids = [
                source.source_id
                for source in sources
                if task.task_id in source.task_ids
            ]
            attempted_queries = task_specific_queries[task.task_id]
            planned_queries = set(task.search_queries)
            planned_queries_attempted = [
                query
                for query in attempted_queries
                if query in planned_queries
            ]
            derived_queries_attempted = [
                query
                for query in attempted_queries
                if query not in planned_queries
            ]
            minimum_query_attempts = min(
                min_queries_per_task,
                len(task.search_queries),
            )
            if not planned_queries_attempted:
                query_coverage = SearchQueryCoverage.NONE
            elif len(planned_queries_attempted) >= minimum_query_attempts:
                query_coverage = SearchQueryCoverage.COMPLETE
            else:
                query_coverage = SearchQueryCoverage.PARTIAL

            relevant_action_ids = list(task_action_ids[task.task_id])
            mapped_source_set = set(mapped_source_ids)
            for source in sources:
                if source.source_id not in mapped_source_set:
                    continue
                relevant_action_ids.extend(
                    action_id
                    for action_id in source.observed_in_action_ids
                    if task.task_id
                    in completed_action_by_id[action_id].scope_task_ids
                )
            relevant_action_ids = _deduplicate(relevant_action_ids)

            unresolved_targets = (
                _deduplicate(draft.unresolved_targets) if draft is not None else []
            )
            coverage_gaps: list[str] = []
            if len(planned_queries_attempted) < minimum_query_attempts:
                coverage_gaps.append(
                    "planned_query_attempts:"
                    f"{len(planned_queries_attempted)}/{minimum_query_attempts}"
                )
            if len(mapped_source_ids) < task.min_sources:
                coverage_gaps.append(
                    f"source_candidates:{len(mapped_source_ids)}/{task.min_sources}"
                )
            source_types = {
                source.source_type
                for source in sources
                if source.source_id in mapped_source_set
            }
            if mapped_source_ids and not source_types.intersection(
                task.preferred_source_types
            ):
                coverage_gaps.append("preferred_source_type_missing")
            candidate_domains = {
                _candidate_domain(source.canonical_url)
                for source in sources
                if source.source_id in mapped_source_set
            }
            candidate_domains.discard("")
            if (
                task.requires_independent_corroboration
                and len(candidate_domains) < 2
            ):
                coverage_gaps.append(
                    f"independent_candidate_domains:{len(candidate_domains)}/2"
                )
            if draft is not None and draft.status == SearchTaskStatus.PARTIAL:
                coverage_gaps.append("model_reported_partial")
            if unresolved_targets:
                coverage_gaps.append(
                    f"unresolved_search_targets:{len(unresolved_targets)}"
                )

            if mapped_source_ids:
                status = (
                    SearchTaskStatus.PARTIAL
                    if coverage_gaps
                    else SearchTaskStatus.SOURCES_FOUND
                )
            elif attempted_queries:
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
                    planned_queries_attempted=_deduplicate(
                        planned_queries_attempted
                    ),
                    derived_queries_attempted=_deduplicate(
                        derived_queries_attempted
                    ),
                    query_coverage=query_coverage,
                    minimum_query_attempts=minimum_query_attempts,
                    minimum_sources=task.min_sources,
                    action_ids=relevant_action_ids,
                    source_ids=mapped_source_ids,
                    coverage_gaps=_deduplicate(coverage_gaps),
                    unresolved_targets=unresolved_targets,
                    notes=draft.notes if draft is not None else "",
                )
            )
        return sources, task_results, warnings, candidate_routes
