"""Executor orchestration: execute Resolver batches and materialize merged state."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from ..llm.protocol import ExtractorProviderError
from ..schemas import (
    CheckerResults,
    DocumentParseStatus,
    ExecutorBatchResult,
    ExecutorBatchStatus,
    ExecutorLimits,
    ExecutorMode,
    ExecutorNextAction,
    ExecutorResults,
    EvidencePassage,
    ExtractionCitation,
    ExtractionResults,
    ExtractionSemanticScope,
    RawExtractionClaim,
    ResearchPlan,
    ResolverAction,
    ResolverResults,
    SearchLimits,
    SearchQueryCoverage,
    SearchResults,
    SearchSource,
    SearchSourceOrigin,
    SearchTaskResult,
    SearchTaskStatus,
    SourceDocument,
)
from .extractor import (
    DEFAULT_MAX_DOCUMENT_BYTES,
    DEFAULT_MAX_DOCUMENT_CHARS,
    DEFAULT_MAX_EVIDENCE_CHARS_PER_CALL,
    DEFAULT_MAX_PASSAGES_PER_TASK,
    DEFAULT_MAX_PDF_SCAN_CHARS,
    ExtractorAgent,
)
from .searcher import SearcherAgent


DEFAULT_MAX_SEARCH_CALLS = 10
DEFAULT_MAX_EXTRACTOR_API_CALLS = 20


class ExecutorValidationError(ValueError):
    """Raised before an inconsistent Resolver execution can be published."""


class ExecutorProviderError(RuntimeError):
    """Preserve child-agent usage when an execution cannot be materialized."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        usages: list,
        extraction_failed_attempts: list | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.usages = list(usages)
        self.extraction_failed_attempts = list(
            extraction_failed_attempts or []
        )


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _artifact_sha256(value) -> str:
    rendered = (
        json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n"
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    material = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _claim_source_ids(extraction: ExtractionResults, claim) -> set[str]:
    citation_by_id = {
        citation.citation_id: citation for citation in extraction.citations
    }
    return {
        citation_by_id[citation_id].source_id
        for citation_id in claim.citation_ids
        if citation_id in citation_by_id
    }


def _successful_semantic_scopes(
    extraction: ExtractionResults,
) -> set[tuple[str, str]]:
    failed_calls = {attempt.call_index for attempt in extraction.failed_attempts}
    scopes = {
        (task_id, source_id)
        for usage in extraction.agent_usage
        if usage.call_index not in failed_calls
        for task_id in usage.scope_task_ids
        for source_id in usage.scope_source_ids
    }
    for claim in extraction.claims:
        for source_id in _claim_source_ids(extraction, claim):
            scopes.add((claim.task_id, source_id))
    return scopes


def _claim_semantic_key(extraction: ExtractionResults, claim) -> tuple:
    return (
        claim.task_id,
        claim.target_field,
        claim.value_text,
        tuple(sorted(_claim_source_ids(extraction, claim))),
    )


def _find_rebased_quote_start(
    document: SourceDocument,
    citation: ExtractionCitation,
) -> int | None:
    starts: list[int] = []
    offset = 0
    while True:
        start = document.text.find(citation.quote, offset)
        if start < 0:
            break
        starts.append(start)
        offset = start + 1
    if not starts:
        return None
    return min(starts, key=lambda start: abs(start - citation.start_char))


def _rebase_additive_claims(
    prior: ExtractionResults,
    delta: ExtractionResults,
    additive_source_ids: set[str],
    merged_document_by_source: dict[str, SourceDocument],
) -> tuple[
    list[EvidencePassage],
    list[ExtractionCitation],
    list[RawExtractionClaim],
    int,
]:
    """Ground new same-content claims in predecessor documents.

    Prior claims keep their stable IDs so Checker history remains usable. New
    claims are added only when their exact quotes still occur in the immutable
    predecessor parse selected for the merged artifact.
    """

    delta_citation_by_id = {
        citation.citation_id: citation for citation in delta.citations
    }
    prior_semantic_keys = {
        _claim_semantic_key(prior, claim) for claim in prior.claims
    }
    passages: list[EvidencePassage] = []
    citations: list[ExtractionCitation] = []
    claims: list[RawExtractionClaim] = []
    discarded = 0

    for claim in delta.claims:
        claim_citations = [
            delta_citation_by_id[citation_id]
            for citation_id in claim.citation_ids
            if citation_id in delta_citation_by_id
        ]
        source_ids = {citation.source_id for citation in claim_citations}
        if (
            not claim_citations
            or len(claim_citations) != len(claim.citation_ids)
            or not source_ids
            or not source_ids.issubset(additive_source_ids)
        ):
            continue
        semantic_key = (
            claim.task_id,
            claim.target_field,
            claim.value_text,
            tuple(sorted(source_ids)),
        )
        if semantic_key in prior_semantic_keys:
            continue

        rebased_citations: list[ExtractionCitation] = []
        rebased_passages: list[EvidencePassage] = []
        for citation in claim_citations:
            document = merged_document_by_source.get(citation.source_id)
            if document is None or claim.task_id not in document.task_ids:
                break
            start = _find_rebased_quote_start(document, citation)
            if start is None:
                break
            end = start + len(citation.quote)
            passage_id = _stable_id(
                "passage", document.document_id, claim.task_id, start, end
            )
            locator = f"chars:{start}-{end}"
            rebased_passages.append(
                EvidencePassage(
                    passage_id=passage_id,
                    document_id=document.document_id,
                    source_id=document.source_id,
                    task_id=claim.task_id,
                    start_char=start,
                    end_char=end,
                    locator=locator,
                    text=citation.quote,
                    matched_terms=[],
                )
            )
            rebased_citations.append(
                ExtractionCitation(
                    citation_id=_stable_id(
                        "citation",
                        passage_id,
                        document.document_id,
                        start,
                        end,
                        citation.quote,
                    ),
                    passage_id=passage_id,
                    document_id=document.document_id,
                    source_id=document.source_id,
                    text_sha256=document.text_sha256 or "",
                    quote=citation.quote,
                    start_char=start,
                    end_char=end,
                    locator=locator,
                )
            )
        else:
            citation_ids = [item.citation_id for item in rebased_citations]
            passages.extend(rebased_passages)
            citations.extend(rebased_citations)
            claims.append(
                claim.model_copy(
                    update={
                        "claim_id": _stable_id(
                            "claim",
                            claim.task_id,
                            claim.target_field,
                            claim.value_text,
                            *citation_ids,
                        ),
                        "citation_ids": citation_ids,
                    }
                )
            )
            prior_semantic_keys.add(semantic_key)
            continue
        discarded += 1

    return passages, citations, claims, discarded


class ExecutorAgent:
    """Run existing Searcher/Extractor workers; Executor itself calls no model."""

    def __init__(
        self,
        searcher: SearcherAgent,
        extractor: ExtractorAgent,
    ) -> None:
        self.searcher = searcher
        self.extractor = extractor

    def execute(
        self,
        plan: ResearchPlan,
        prior_search: SearchResults,
        prior_extraction: ExtractionResults,
        checker: CheckerResults,
        resolution: ResolverResults,
        *,
        plan_sha256: str,
        prior_search_sha256: str,
        prior_extraction_sha256: str,
        check_sha256: str,
        resolution_sha256: str,
        plan_reference: str,
        prior_search_reference: str,
        prior_extraction_reference: str,
        check_reference: str,
        resolution_reference: str,
        merged_search_reference: str,
        merged_extraction_reference: str,
        iteration: int,
        execution_mode: ExecutorMode,
        max_search_calls: int = DEFAULT_MAX_SEARCH_CALLS,
        min_queries_per_task: int = 1,
        max_retry_tasks: int = 0,
        retry_search_calls: int = 1,
        max_document_bytes: int = DEFAULT_MAX_DOCUMENT_BYTES,
        max_document_chars: int = DEFAULT_MAX_DOCUMENT_CHARS,
        max_pdf_scan_chars: int = DEFAULT_MAX_PDF_SCAN_CHARS,
        max_passages_per_task: int = DEFAULT_MAX_PASSAGES_PER_TASK,
        max_evidence_chars_per_call: int = DEFAULT_MAX_EVIDENCE_CHARS_PER_CALL,
        max_extractor_api_calls: int = DEFAULT_MAX_EXTRACTOR_API_CALLS,
    ) -> tuple[SearchResults, ExtractionResults, ExecutorResults]:
        self._validate_inputs(
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
            iteration=iteration,
            execution_mode=execution_mode,
        )

        query_overrides = {
            task_id: _deduplicate(
                [
                    query
                    for item in resolution.work_items
                    if item.selected_action == ResolverAction.SEARCH_NEW_SOURCE
                    and item.task_id == task_id
                    for query in item.queries
                ]
            )
            for task_id in resolution.search_task_ids
        }
        delta_search = None
        if resolution.search_task_ids:
            delta_search = self.searcher.create_search_results(
                plan,
                plan_sha256=plan_sha256,
                plan_reference=plan_reference,
                iteration=iteration,
                requested_task_ids=resolution.search_task_ids,
                task_limit=len(resolution.search_task_ids),
                max_search_calls=max_search_calls,
                min_queries_per_task=min_queries_per_task,
                max_retry_tasks=max_retry_tasks,
                retry_search_calls=retry_search_calls,
                query_overrides=query_overrides,
            )

        try:
            merged_search = self._merge_search(
                plan,
                prior_search,
                delta_search,
                plan_sha256=plan_sha256,
                plan_reference=plan_reference,
                prior_search_sha256=prior_search_sha256,
                prior_search_reference=prior_search_reference,
                resolution=resolution,
                resolution_sha256=resolution_sha256,
                resolution_reference=resolution_reference,
                execution_mode=execution_mode,
                iteration=iteration,
                max_search_calls=max_search_calls,
                min_queries_per_task=min_queries_per_task,
                max_retry_tasks=max_retry_tasks,
                retry_search_calls=retry_search_calls,
                query_overrides=query_overrides,
            )
        except Exception as exc:
            if delta_search is None or not delta_search.agent_usage:
                raise
            raise ExecutorProviderError(
                "Paid Searcher completed but Executor search merge failed "
                f"({type(exc).__name__}).",
                code="postprocessing_error",
                usages=delta_search.agent_usage,
            ) from None
        merged_search_sha256 = _artifact_sha256(merged_search)

        action_by_source: dict[str, set[ResolverAction]] = {}
        for item in resolution.work_items:
            for source_id in item.selected_source_ids:
                action_by_source.setdefault(source_id, set()).add(
                    item.selected_action
                )
        retried_source_ids = [
            source_id
            for source_id in resolution.execution_source_ids
            if ResolverAction.RETRY_RETRIEVAL
            in action_by_source.get(source_id, set())
        ]
        delta_source_ids = (
            [source.source_id for source in delta_search.sources]
            if delta_search is not None
            else []
        )
        process_source_ids = _deduplicate(
            [*resolution.execution_source_ids, *delta_source_ids]
        )
        known_merged_source_ids = {
            source.source_id for source in merged_search.sources
        }
        process_source_ids = [
            source_id
            for source_id in process_source_ids
            if source_id in known_merged_source_ids
        ]
        retried_source_set = set(retried_source_ids)
        cache_source_ids = [
            source_id
            for source_id in process_source_ids
            if source_id not in retried_source_set
        ]
        prior_document_by_source = {
            document.source_id: document
            for document in prior_extraction.documents
        }
        merged_source_by_id = {
            source.source_id: source for source in merged_search.sources
        }
        cached_documents = [
            prior_document_by_source[source_id].model_copy(
                update={
                    "task_ids": merged_source_by_id[source_id].task_ids,
                }
            )
            for source_id in cache_source_ids
            if source_id in prior_document_by_source
        ]

        delta_extraction = None
        if process_source_ids:
            try:
                delta_extraction = self.extractor.create_extraction_results(
                    plan,
                    merged_search,
                    plan_sha256=plan_sha256,
                    search_sha256=merged_search_sha256,
                    search_reference=merged_search_reference,
                    plan_reference=plan_reference,
                    iteration=iteration,
                    requested_source_ids=process_source_ids,
                    source_limit=None,
                    max_document_bytes=max_document_bytes,
                    max_document_chars=max_document_chars,
                    max_pdf_scan_chars=max_pdf_scan_chars,
                    max_passages_per_task=max_passages_per_task,
                    max_evidence_chars_per_call=max_evidence_chars_per_call,
                    max_api_calls=max_extractor_api_calls,
                    cached_documents=cached_documents,
                    cached_document_search_id=prior_search.search_id,
                    cached_document_origin=(
                        "the exact predecessor Extractor artifact recorded by Executor"
                    ),
                    trust_cached_document_ids=True,
                )
            except ExtractorProviderError as exc:
                raise ExecutorProviderError(
                    "Executor Extractor child failed; all known child usage "
                    "must be persisted.",
                    code=exc.code,
                    usages=[
                        *(delta_search.agent_usage if delta_search else []),
                        *exc.usages,
                    ],
                    extraction_failed_attempts=exc.failed_attempts,
                ) from None

        try:
            merged_extraction = self._merge_extraction(
                plan,
                merged_search,
                prior_extraction,
                delta_extraction,
                process_source_ids=process_source_ids,
                plan_sha256=plan_sha256,
                merged_search_sha256=merged_search_sha256,
                plan_reference=plan_reference,
                merged_search_reference=merged_search_reference,
                prior_extraction_sha256=prior_extraction_sha256,
                prior_extraction_reference=prior_extraction_reference,
                resolution=resolution,
                resolution_sha256=resolution_sha256,
                resolution_reference=resolution_reference,
                execution_mode=execution_mode,
                iteration=iteration,
            )
        except Exception as exc:
            usages = [
                *(delta_search.agent_usage if delta_search else []),
                *(delta_extraction.agent_usage if delta_extraction else []),
            ]
            if not usages:
                raise
            raise ExecutorProviderError(
                "Paid child calls completed but Executor extraction merge failed "
                f"({type(exc).__name__}).",
                code="postprocessing_error",
                usages=usages,
                extraction_failed_attempts=(
                    delta_extraction.failed_attempts if delta_extraction else []
                ),
            ) from None
        merged_extraction_sha256 = _artifact_sha256(merged_extraction)

        prior_source_ids = {source.source_id for source in prior_search.sources}
        new_source_ids = [
            source_id
            for source_id in delta_source_ids
            if source_id not in prior_source_ids
        ]
        inherited_source_ids = [
            source_id
            for source_id in merged_extraction.selected_source_ids
            if source_id not in set(process_source_ids)
        ]
        preserved_processed_source_ids = (
            merged_extraction.preserved_processed_source_ids
        )
        batch_results = self._build_batch_results(
            resolution,
            merged_search,
            merged_extraction,
            execution_mode=execution_mode,
            delta_search=delta_search,
            delta_extraction=delta_extraction,
        )
        agent_usage = [
            *(delta_search.agent_usage if delta_search is not None else []),
            *(
                delta_extraction.agent_usage
                if delta_extraction is not None
                else []
            ),
        ]
        warnings = _deduplicate(
            [
                "Executor materialized a merged research state; predecessor "
                "artifacts remain immutable and authoritative for inherited provenance.",
                *(
                    f"Searcher delta: {warning}"
                    for warning in (
                        delta_search.warnings if delta_search is not None else []
                    )
                ),
                *(
                    f"Extractor delta: {warning}"
                    for warning in (
                        delta_extraction.warnings
                        if delta_extraction is not None
                        else []
                    )
                ),
            ]
        )
        automated = any(
            batch.action != ResolverAction.HUMAN_REVIEW
            for batch in resolution.execution_batches
        )
        results = ExecutorResults(
            execution_id=str(uuid4()),
            plan_run_id=plan.run_id,
            search_id=prior_search.search_id,
            extraction_id=prior_extraction.extraction_id,
            check_id=checker.check_id,
            resolution_id=resolution.resolution_id,
            plan_sha256=plan_sha256,
            prior_search_sha256=prior_search_sha256,
            prior_extraction_sha256=prior_extraction_sha256,
            check_sha256=check_sha256,
            resolution_sha256=resolution_sha256,
            plan_reference=plan_reference,
            prior_search_reference=prior_search_reference,
            prior_extraction_reference=prior_extraction_reference,
            check_reference=check_reference,
            resolution_reference=resolution_reference,
            created_at=datetime.now(timezone.utc),
            iteration=iteration,
            execution_mode=execution_mode,
            brand_name=plan.planner_input.brand_name,
            target_country=plan.planner_input.target_country,
            depth=plan.planner_input.depth,
            limits=ExecutorLimits(
                max_search_calls=max_search_calls,
                min_queries_per_task=min_queries_per_task,
                max_retry_tasks=max_retry_tasks,
                retry_search_calls=retry_search_calls,
                max_document_bytes=max_document_bytes,
                max_document_chars=max_document_chars,
                max_pdf_scan_chars=max_pdf_scan_chars,
                max_passages_per_task=max_passages_per_task,
                max_evidence_chars_per_call=max_evidence_chars_per_call,
                max_extractor_api_calls=max_extractor_api_calls,
            ),
            batch_results=batch_results,
            processed_source_ids=process_source_ids,
            retried_source_ids=retried_source_ids,
            cached_source_ids=[
                document.source_id for document in cached_documents
            ],
            preserved_processed_source_ids=preserved_processed_source_ids,
            new_source_ids=new_source_ids,
            inherited_source_ids=inherited_source_ids,
            pending_human_follow_up_ids=_deduplicate(
                [
                    follow_up_id
                    for batch in resolution.execution_batches
                    if batch.action == ResolverAction.HUMAN_REVIEW
                    for follow_up_id in batch.follow_up_ids
                ]
            ),
            search_executed=bool(
                delta_search is not None and delta_search.search_executed
            ),
            network_executed=bool(
                delta_extraction is not None
                and delta_extraction.network_executed
            ),
            provider_executed=bool(
                (delta_search is not None and delta_search.search_executed)
                or (
                    delta_extraction is not None
                    and delta_extraction.provider_executed
                )
            ),
            merged_search_id=merged_search.search_id,
            merged_search_sha256=merged_search_sha256,
            merged_search_reference=merged_search_reference,
            merged_extraction_id=merged_extraction.extraction_id,
            merged_extraction_sha256=merged_extraction_sha256,
            merged_extraction_reference=merged_extraction_reference,
            ready_for_checker=automated,
            recommended_next_action=(
                ExecutorNextAction.RUN_CHECKER
                if automated
                else ExecutorNextAction.HUMAN_REVIEW
            ),
            warnings=warnings,
            compliance_rules=_deduplicate(
                [
                    *resolution.compliance_rules,
                    "Executor never treats a planned action as evidence of a "
                    "successful retrieval, search, or extraction.",
                    "Inherited facts retain exact predecessor lineage; a processed "
                    "source replaces predecessor evidence only after a usable paid "
                    "semantic result, otherwise the prior state is preserved.",
                    "Run Checker on merged_extraction_reference before Normalizer.",
                ]
            ),
            agent_usage=agent_usage,
        )
        return merged_search, merged_extraction, results

    @staticmethod
    def reconcile_extraction(
        plan: ResearchPlan,
        merged_search: SearchResults,
        prior: ExtractionResults,
        current: ExtractionResults,
        resolution: ResolverResults,
        *,
        plan_sha256: str,
        merged_search_sha256: str,
        prior_extraction_sha256: str,
        current_extraction_sha256: str,
        resolution_sha256: str,
        plan_reference: str,
        merged_search_reference: str,
        prior_extraction_reference: str,
        current_extraction_reference: str,
        resolution_reference: str,
    ) -> ExtractionResults:
        """Repair an existing Executor merge without new external work."""

        if current.generated_by != "executor":
            raise ExecutorValidationError(
                "Only an Executor extraction artifact can be reconciled."
            )
        if current.reconciled_from_extraction_id is not None:
            raise ExecutorValidationError(
                "The supplied extraction is already a reconciliation artifact."
            )
        if (
            current.plan_run_id != plan.run_id
            or current.plan_sha256 != plan_sha256
            or current.search_id != merged_search.search_id
            or current.search_sha256 != merged_search_sha256
            or current.prior_extraction_id != prior.extraction_id
            or current.prior_extraction_sha256 != prior_extraction_sha256
            or current.resolution_id != resolution.resolution_id
            or current.resolution_sha256 != resolution_sha256
        ):
            raise ExecutorValidationError(
                "Reconciliation inputs do not match the current artifact lineage."
            )
        try:
            execution_mode = ExecutorMode(current.execution_mode)
        except (TypeError, ValueError) as exc:
            raise ExecutorValidationError(
                "Current Executor extraction has an invalid execution mode."
            ) from exc

        return ExecutorAgent._merge_extraction(
            plan,
            merged_search,
            prior,
            current,
            process_source_ids=current.processed_source_ids,
            plan_sha256=plan_sha256,
            merged_search_sha256=merged_search_sha256,
            plan_reference=plan_reference,
            merged_search_reference=merged_search_reference,
            prior_extraction_sha256=prior_extraction_sha256,
            prior_extraction_reference=prior_extraction_reference,
            resolution=resolution,
            resolution_sha256=resolution_sha256,
            resolution_reference=resolution_reference,
            execution_mode=execution_mode,
            iteration=current.iteration,
            reconciled_from_extraction_id=current.extraction_id,
            reconciled_from_extraction_sha256=current_extraction_sha256,
            reconciled_from_extraction_reference=current_extraction_reference,
        )

    @staticmethod
    def _merge_search(
        plan: ResearchPlan,
        prior: SearchResults,
        delta: SearchResults | None,
        *,
        plan_sha256: str,
        plan_reference: str,
        prior_search_sha256: str,
        prior_search_reference: str,
        resolution: ResolverResults,
        resolution_sha256: str,
        resolution_reference: str,
        execution_mode: ExecutorMode,
        iteration: int,
        max_search_calls: int,
        min_queries_per_task: int,
        max_retry_tasks: int,
        retry_search_calls: int,
        query_overrides: dict[str, list[str]],
    ) -> SearchResults:
        materialized_query_overrides = (
            delta.limits.query_overrides
            if delta is not None
            else query_overrides
        )
        prior_by_id = {source.source_id: source for source in prior.sources}
        delta_by_id = {
            source.source_id: source for source in (delta.sources if delta else [])
        }
        sources: list[SearchSource] = []
        for source in prior.sources:
            delta_source = delta_by_id.get(source.source_id)
            sources.append(
                source.model_copy(
                    update={
                        "origin": SearchSourceOrigin.INHERITED,
                        "provider_observed": False,
                        "task_ids": _deduplicate(
                            [
                                *source.task_ids,
                                *(delta_source.task_ids if delta_source else []),
                            ]
                        ),
                        "observed_in_action_ids": [],
                        "discovered_via_queries": [],
                        "relevance_note": (
                            "Inherited from exact predecessor Searcher artifact. "
                            f"{source.relevance_note}"
                        )[:1000],
                        "inherited_from_search_id": prior.search_id,
                    }
                )
            )
        sources.extend(
            source
            for source in (delta.sources if delta else [])
            if source.source_id not in prior_by_id
        )
        selected_task_ids = _deduplicate(
            [
                *prior.selected_task_ids,
                *(delta.selected_task_ids if delta else []),
            ]
        )
        source_ids_by_task = {
            task_id: [
                source.source_id
                for source in sources
                if task_id in source.task_ids
            ]
            for task_id in selected_task_ids
        }
        prior_tasks = {item.task_id: item for item in prior.task_results}
        delta_tasks = {
            item.task_id: item for item in (delta.task_results if delta else [])
        }
        task_results: list[SearchTaskResult] = []
        for task_id in selected_task_ids:
            source_ids = source_ids_by_task[task_id]
            if task_id in delta_tasks:
                base = delta_tasks[task_id]
                status = base.status
                coverage_gaps = list(base.coverage_gaps)
                if source_ids and status in {
                    SearchTaskStatus.NO_SOURCES_FOUND,
                    SearchTaskStatus.NOT_SEARCHED,
                    SearchTaskStatus.QUERY_WORKLOAD_ONLY,
                }:
                    status = SearchTaskStatus.PARTIAL
                    coverage_gaps.append("inherited_source_candidates")
                query_coverage = (
                    SearchQueryCoverage.NONE
                    if status == SearchTaskStatus.PARTIAL
                    and not base.attempted_queries
                    else base.query_coverage
                )
                task_results.append(
                    base.model_copy(
                        update={
                            "status": status,
                            "query_coverage": query_coverage,
                            "source_ids": source_ids,
                            "coverage_gaps": _deduplicate(coverage_gaps),
                            "notes": (
                                "Executor search delta merged with predecessor "
                                "source candidates."
                            ),
                        }
                    )
                )
                continue
            base = prior_tasks[task_id]
            task_results.append(
                base.model_copy(
                    update={
                        "status": (
                            SearchTaskStatus.PARTIAL
                            if source_ids
                            else SearchTaskStatus.NOT_SEARCHED
                        ),
                        "attempted_queries": [],
                        "planned_queries_attempted": [],
                        "derived_queries_attempted": [],
                        "query_coverage": SearchQueryCoverage.NONE,
                        "action_ids": [],
                        "source_ids": source_ids,
                        "coverage_gaps": _deduplicate(
                            [*base.coverage_gaps, "inherited_search_state"]
                        ),
                        "notes": (
                            "No new search was scheduled for this task; candidates "
                            "are inherited through exact predecessor lineage."
                        ),
                    }
                )
            )
        return SearchResults(
            schema_version="1.2.0",
            search_id=str(uuid4()),
            plan_run_id=plan.run_id,
            plan_sha256=plan_sha256,
            plan_reference=plan_reference,
            created_at=datetime.now(timezone.utc),
            iteration=iteration,
            generated_by="executor",
            model=delta.model if delta is not None else None,
            execution_mode=execution_mode.value,
            resolution_id=resolution.resolution_id,
            resolution_sha256=resolution_sha256,
            resolution_reference=resolution_reference,
            prior_search_id=prior.search_id,
            prior_search_sha256=prior_search_sha256,
            prior_search_reference=prior_search_reference,
            brand_name=plan.planner_input.brand_name,
            target_country=plan.planner_input.target_country,
            depth=plan.planner_input.depth,
            search_executed=bool(delta and delta.search_executed),
            limits=SearchLimits(
                max_search_calls=max_search_calls,
                task_limit=(len(resolution.search_task_ids) or None),
                requested_task_ids=resolution.search_task_ids,
                min_queries_per_task=min_queries_per_task,
                max_retry_tasks=max_retry_tasks,
                retry_search_calls=retry_search_calls,
                query_overrides=materialized_query_overrides,
            ),
            selected_task_ids=selected_task_ids,
            unselected_task_ids=[
                task.task_id
                for task in plan.tasks
                if task.task_id not in set(selected_task_ids)
            ],
            actions=list(delta.actions if delta else []),
            sources=sources,
            task_results=task_results,
            warnings=_deduplicate(
                [
                    "Materialized Executor merge; inherited source provenance "
                    "resolves through prior_search_reference.",
                    *(delta.warnings if delta else []),
                ]
            ),
            compliance_rules=_deduplicate(
                [*prior.compliance_rules, *(delta.compliance_rules if delta else [])]
            ),
            agent_usage=list(delta.agent_usage if delta else []),
            failed_attempts=list(delta.failed_attempts if delta else []),
        )

    @staticmethod
    def _merge_extraction(
        plan: ResearchPlan,
        merged_search: SearchResults,
        prior: ExtractionResults,
        delta: ExtractionResults | None,
        *,
        process_source_ids: list[str],
        plan_sha256: str,
        merged_search_sha256: str,
        plan_reference: str,
        merged_search_reference: str,
        prior_extraction_sha256: str,
        prior_extraction_reference: str,
        resolution: ResolverResults,
        resolution_sha256: str,
        resolution_reference: str,
        execution_mode: ExecutorMode,
        iteration: int,
        reconciled_from_extraction_id: str | None = None,
        reconciled_from_extraction_sha256: str | None = None,
        reconciled_from_extraction_reference: str | None = None,
    ) -> ExtractionResults:
        processed = set(process_source_ids)
        delta_document_by_source = {
            document.source_id: document
            for document in (delta.documents if delta else [])
        }
        prior_document_by_source = {
            document.source_id: document for document in prior.documents
        }
        merged_source_by_id = {
            source.source_id: source for source in merged_search.sources
        }
        selected_source_ids = _deduplicate(
            [
                *prior.selected_source_ids,
                *(delta.selected_source_ids if delta else []),
            ]
        )
        selected_source_ids = [
            source_id
            for source_id in selected_source_ids
            if source_id in {source.source_id for source in merged_search.sources}
            and (
                source_id in delta_document_by_source
                or source_id in prior_document_by_source
            )
        ]
        delta_successful_source_ids = {
            source_id
            for _, source_id in (
                _successful_semantic_scopes(delta) if delta is not None else set()
            )
        }
        replacement_source_ids: set[str] = set()
        additive_same_content_source_ids: set[str] = set()
        for source_id in process_source_ids:
            delta_document = delta_document_by_source.get(source_id)
            if delta_document is None:
                continue
            if source_id not in prior_document_by_source:
                replacement_source_ids.add(source_id)
                continue
            source = merged_source_by_id[source_id]
            if (
                execution_mode == ExecutorMode.PAID
                and delta_document.parse_status
                in {DocumentParseStatus.PARSED, DocumentParseStatus.PARTIAL}
                and (
                    source_id in delta_successful_source_ids
                    or source.source_type.value == "routing_lead"
                )
            ):
                prior_document = prior_document_by_source[source_id]
                if (
                    prior_document.content_sha256 is not None
                    and prior_document.content_sha256
                    == delta_document.content_sha256
                ):
                    additive_same_content_source_ids.add(source_id)
                else:
                    replacement_source_ids.add(source_id)
        preserved_processed_source_ids = [
            source_id
            for source_id in process_source_ids
            if source_id in prior_document_by_source
            and source_id not in replacement_source_ids
        ]
        documents = [
            (
                delta_document_by_source[source_id]
                if source_id in replacement_source_ids
                else prior_document_by_source[source_id]
            ).model_copy(
                update={
                    "task_ids": merged_source_by_id[source_id].task_ids,
                }
            )
            for source_id in selected_source_ids
        ]
        merged_document_by_source = {
            document.source_id: document for document in documents
        }
        inherited_source_ids = [
            source_id for source_id in selected_source_ids if source_id not in processed
        ]
        preserved_evidence_source_ids = {
            source_id
            for source_id in prior.selected_source_ids
            if source_id not in replacement_source_ids
        }
        kept_passages = [
            passage
            for passage in prior.evidence_passages
            if passage.source_id in preserved_evidence_source_ids
        ]
        kept_citations = [
            citation
            for citation in prior.citations
            if citation.source_id in preserved_evidence_source_ids
        ]
        kept_citation_ids = {citation.citation_id for citation in kept_citations}
        kept_claims = [
            claim
            for claim in prior.claims
            if set(claim.citation_ids).issubset(kept_citation_ids)
        ]
        direct_delta_passages = [
            passage
            for passage in (delta.evidence_passages if delta else [])
            if passage.source_id in replacement_source_ids
        ]
        direct_delta_citations = [
            citation
            for citation in (delta.citations if delta else [])
            if citation.source_id in replacement_source_ids
        ]
        direct_delta_citation_ids = {
            citation.citation_id for citation in direct_delta_citations
        }
        direct_delta_claims = [
            claim
            for claim in (delta.claims if delta else [])
            if set(claim.citation_ids).issubset(direct_delta_citation_ids)
        ]
        (
            rebased_passages,
            rebased_citations,
            rebased_claims,
            discarded_rebased_claims,
        ) = (
            _rebase_additive_claims(
                prior,
                delta,
                additive_same_content_source_ids,
                merged_document_by_source,
            )
            if delta is not None and additive_same_content_source_ids
            else ([], [], [], 0)
        )
        passages = list(
            {
                passage.passage_id: passage
                for passage in [
                    *kept_passages,
                    *direct_delta_passages,
                    *rebased_passages,
                ]
            }.values()
        )
        citations = list(
            {
                citation.citation_id: citation
                for citation in [
                    *kept_citations,
                    *direct_delta_citations,
                    *rebased_citations,
                ]
            }.values()
        )
        claims = list(
            {
                claim.claim_id: claim
                for claim in [
                    *kept_claims,
                    *direct_delta_claims,
                    *rebased_claims,
                ]
            }.values()
        )
        selected_task_id_set = {
            task_id for document in documents for task_id in document.task_ids
        }
        selected_tasks = [
            task for task in plan.tasks if task.task_id in selected_task_id_set
        ]
        selected_task_ids = [task.task_id for task in selected_tasks]
        selected_sources = [
            merged_source_by_id[source_id] for source_id in selected_source_ids
        ]
        semantic_scopes = _successful_semantic_scopes(prior)
        if delta is not None:
            semantic_scopes -= {
                scope
                for scope in semantic_scopes
                if scope[1] in replacement_source_ids
            }
            semantic_scopes |= {
                scope
                for scope in _successful_semantic_scopes(delta)
                if scope[1]
                in (replacement_source_ids | additive_same_content_source_ids)
            }
        task_results = ExtractorAgent._build_task_results(
            selected_tasks,
            selected_sources,
            documents,
            passages,
            claims,
            merged_search,
            paid=True,
            semantically_processed_task_sources=semantic_scopes,
        )
        limits = (
            delta.limits if delta is not None else prior.limits.model_copy(
                update={"requested_source_ids": [], "source_limit": None}
            )
        )
        return ExtractionResults(
            schema_version="1.2.0",
            extraction_id=str(uuid4()),
            plan_run_id=plan.run_id,
            search_id=merged_search.search_id,
            plan_sha256=plan_sha256,
            search_sha256=merged_search_sha256,
            plan_reference=plan_reference,
            search_reference=merged_search_reference,
            created_at=datetime.now(timezone.utc),
            iteration=iteration,
            generated_by="executor",
            model=(delta.model if delta and delta.model else prior.model),
            execution_mode=execution_mode.value,
            resolution_id=resolution.resolution_id,
            resolution_sha256=resolution_sha256,
            resolution_reference=resolution_reference,
            prior_extraction_id=prior.extraction_id,
            prior_extraction_sha256=prior_extraction_sha256,
            prior_extraction_reference=prior_extraction_reference,
            reconciled_from_extraction_id=reconciled_from_extraction_id,
            reconciled_from_extraction_sha256=(
                reconciled_from_extraction_sha256
            ),
            reconciled_from_extraction_reference=(
                reconciled_from_extraction_reference
            ),
            inherited_source_ids=inherited_source_ids,
            processed_source_ids=[
                source_id for source_id in selected_source_ids if source_id in processed
            ],
            preserved_processed_source_ids=preserved_processed_source_ids,
            semantically_processed_scopes=[
                ExtractionSemanticScope(task_id=task_id, source_id=source_id)
                for task_id, source_id in sorted(semantic_scopes)
                if task_id in set(selected_task_ids)
                and source_id in set(selected_source_ids)
            ],
            brand_name=plan.planner_input.brand_name,
            target_country=plan.planner_input.target_country,
            depth=plan.planner_input.depth,
            network_executed=bool(delta and delta.network_executed),
            provider_executed=bool(delta and delta.provider_executed),
            limits=limits,
            selected_task_ids=selected_task_ids,
            selected_source_ids=selected_source_ids,
            unselected_source_ids=[
                source.source_id
                for source in merged_search.sources
                if source.source_id not in set(selected_source_ids)
            ],
            documents=documents,
            evidence_passages=passages,
            citations=citations,
            claims=claims,
            task_results=task_results,
            warnings=_deduplicate(
                [
                    "Merged predecessor extraction with current Resolver execution; "
                    "changed content may supersede prior evidence, while identical "
                    "raw content is merged additively.",
                    *(
                        [
                            "Preserved checked predecessor evidence and added newly "
                            "grounded claims for "
                            f"{len(additive_same_content_source_ids)} source(s) with "
                            "identical raw content."
                        ]
                        if additive_same_content_source_ids
                        else []
                    ),
                    *(
                        [
                            "Discarded "
                            f"{discarded_rebased_claims} additive claim(s) because "
                            "their exact quotes were absent from the preserved parse."
                        ]
                        if discarded_rebased_claims
                        else []
                    ),
                    *(
                        [
                            "Offline reconciliation materialized a new immutable "
                            "artifact; it made no provider or network calls."
                        ]
                        if reconciled_from_extraction_id is not None
                        else []
                    ),
                    *(
                        [
                            "Preserved predecessor evidence for "
                            f"{len(preserved_processed_source_ids)} processed "
                            "source(s) because free mode, an unusable replacement, "
                            "or identical raw content made preservation preferable."
                        ]
                        if preserved_processed_source_ids
                        else []
                    ),
                    *(delta.warnings if delta else []),
                ]
            ),
            compliance_rules=_deduplicate(
                [*prior.compliance_rules, *(delta.compliance_rules if delta else [])]
            ),
            agent_usage=list(delta.agent_usage if delta else []),
            failed_attempts=list(delta.failed_attempts if delta else []),
        )

    @staticmethod
    def _build_batch_results(
        resolution: ResolverResults,
        merged_search: SearchResults,
        merged_extraction: ExtractionResults,
        *,
        execution_mode: ExecutorMode,
        delta_search: SearchResults | None,
        delta_extraction: ExtractionResults | None,
    ) -> list[ExecutorBatchResult]:
        document_by_source = {
            document.source_id: document
            for document in merged_extraction.documents
        }
        outcome_document_by_source = {
            document.source_id: document
            for document in (
                delta_extraction.documents if delta_extraction is not None else []
            )
        }
        successfully_extracted_source_ids = {
            source_id
            for _, source_id in (
                _successful_semantic_scopes(delta_extraction)
                if delta_extraction is not None
                else set()
            )
        }
        merged_source_by_id = {
            source.source_id: source for source in merged_search.sources
        }
        claim_sources = {
            claim.claim_id: _claim_source_ids(merged_extraction, claim)
            for claim in merged_extraction.claims
        }
        prior_source_ids = {
            source.source_id
            for source in merged_search.sources
            if source.origin == SearchSourceOrigin.INHERITED
        }
        results: list[ExecutorBatchResult] = []
        for batch in resolution.execution_batches:
            if batch.action == ResolverAction.HUMAN_REVIEW:
                results.append(
                    ExecutorBatchResult(
                        batch_id=batch.batch_id,
                        action=batch.action,
                        status=ExecutorBatchStatus.PENDING_HUMAN,
                        resolution_item_ids=batch.resolution_item_ids,
                        follow_up_ids=batch.follow_up_ids,
                        task_ids=batch.task_ids,
                        requested_source_ids=batch.source_ids,
                        requested_queries=batch.queries,
                        warnings=[
                            "Human-review work was preserved and not automated."
                        ],
                    )
                )
                continue
            if batch.action == ResolverAction.SEARCH_NEW_SOURCE:
                resulting_source_ids = [
                    source.source_id
                    for source in merged_search.sources
                    if source.source_id not in prior_source_ids
                    and set(source.task_ids).intersection(batch.task_ids)
                ]
                status = (
                    ExecutorBatchStatus.COMPLETED
                    if delta_search is not None and delta_search.search_executed
                    else ExecutorBatchStatus.PARTIAL
                )
                warnings = (
                    []
                    if status == ExecutorBatchStatus.COMPLETED
                    else [
                        "Free Executor prepared Resolver queries but cannot execute "
                        "OpenAI web search."
                    ]
                )
                results.append(
                    ExecutorBatchResult(
                        batch_id=batch.batch_id,
                        action=batch.action,
                        status=status,
                        resolution_item_ids=batch.resolution_item_ids,
                        follow_up_ids=batch.follow_up_ids,
                        task_ids=batch.task_ids,
                        requested_source_ids=batch.source_ids,
                        requested_queries=batch.queries,
                        resulting_source_ids=resulting_source_ids,
                        resulting_document_ids=[
                            document_by_source[source_id].document_id
                            for source_id in resulting_source_ids
                            if source_id in document_by_source
                        ],
                        resulting_claim_ids=[
                            claim_id
                            for claim_id, source_ids in claim_sources.items()
                            if source_ids.intersection(resulting_source_ids)
                        ],
                        warnings=warnings,
                    )
                )
                continue
            resulting_document_ids = [
                document_by_source[source_id].document_id
                for source_id in batch.source_ids
                if source_id in document_by_source
            ]
            accessible = bool(resulting_document_ids) and all(
                outcome_document_by_source.get(
                    source_id,
                    document_by_source[source_id],
                ).parse_status
                in {DocumentParseStatus.PARSED, DocumentParseStatus.PARTIAL}
                for source_id in batch.source_ids
                if source_id in document_by_source
            )
            semantic_action = batch.action in {
                ResolverAction.EXTRACT_KNOWN_SOURCE,
                ResolverAction.REEXTRACT_EXISTING,
            }
            semantic_completed = all(
                source_id in successfully_extracted_source_ids
                or merged_source_by_id[source_id].source_type.value
                == "routing_lead"
                for source_id in batch.source_ids
                if source_id in merged_source_by_id
            )
            completed = accessible and not (
                execution_mode == ExecutorMode.FREE and semantic_action
            ) and (not semantic_action or semantic_completed)
            results.append(
                ExecutorBatchResult(
                    batch_id=batch.batch_id,
                    action=batch.action,
                    status=(
                        ExecutorBatchStatus.COMPLETED
                        if completed
                        else ExecutorBatchStatus.PARTIAL
                    ),
                    resolution_item_ids=batch.resolution_item_ids,
                    follow_up_ids=batch.follow_up_ids,
                    task_ids=batch.task_ids,
                    requested_source_ids=batch.source_ids,
                    requested_queries=batch.queries,
                    resulting_source_ids=[
                        source_id
                        for source_id in batch.source_ids
                        if source_id in document_by_source
                    ],
                    resulting_document_ids=resulting_document_ids,
                    resulting_claim_ids=[
                        claim_id
                        for claim_id, source_ids in claim_sources.items()
                        if source_ids.intersection(batch.source_ids)
                    ],
                    warnings=(
                        [
                            "Free Executor retrieved/prepared content but did not "
                            "perform semantic extraction."
                        ]
                        if execution_mode == ExecutorMode.FREE and semantic_action
                        else []
                        if completed
                        else [
                            "At least one scheduled document remained inaccessible "
                            "or was not semantically processed within the API cap."
                        ]
                    ),
                )
            )
        return results

    @staticmethod
    def _validate_inputs(
        plan: ResearchPlan,
        prior_search: SearchResults,
        prior_extraction: ExtractionResults,
        checker: CheckerResults,
        resolution: ResolverResults,
        *,
        plan_sha256: str,
        prior_search_sha256: str,
        prior_extraction_sha256: str,
        check_sha256: str,
        resolution_sha256: str,
        iteration: int,
        execution_mode: ExecutorMode,
    ) -> None:
        del execution_mode
        for value, label in (
            (plan_sha256, "plan"),
            (prior_search_sha256, "prior Searcher"),
            (prior_extraction_sha256, "prior Extractor"),
            (check_sha256, "Checker"),
            (resolution_sha256, "Resolver"),
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ExecutorValidationError(
                    f"{label} SHA-256 must be a lowercase hexadecimal digest."
                )
        if iteration <= resolution.iteration:
            raise ExecutorValidationError(
                "Executor iteration must be greater than the Resolver iteration."
            )
        if not resolution.ready_for_execution:
            raise ExecutorValidationError("Resolver is not ready for execution.")
        if (
            prior_search.plan_run_id != plan.run_id
            or prior_extraction.plan_run_id != plan.run_id
            or checker.plan_run_id != plan.run_id
            or resolution.plan_run_id != plan.run_id
        ):
            raise ExecutorValidationError("Executor lineage uses different plan runs.")
        if (
            prior_search.plan_sha256 != plan_sha256
            or prior_extraction.plan_sha256 != plan_sha256
            or checker.plan_sha256 != plan_sha256
            or resolution.plan_sha256 != plan_sha256
        ):
            raise ExecutorValidationError("Executor plan SHA-256 lineage mismatch.")
        if (
            prior_extraction.search_id != prior_search.search_id
            or checker.search_id != prior_search.search_id
            or resolution.search_id != prior_search.search_id
            or resolution.search_sha256 != prior_search_sha256
            or checker.search_sha256 != prior_search_sha256
        ):
            raise ExecutorValidationError("Executor Searcher lineage mismatch.")
        if (
            checker.extraction_id != prior_extraction.extraction_id
            or resolution.extraction_id != prior_extraction.extraction_id
            or resolution.extraction_sha256 != prior_extraction_sha256
            or checker.extraction_sha256 != prior_extraction_sha256
        ):
            raise ExecutorValidationError("Executor Extractor lineage mismatch.")
        if (
            resolution.check_id != checker.check_id
            or resolution.check_sha256 != check_sha256
        ):
            raise ExecutorValidationError("Executor Checker lineage mismatch.")
        try:
            parsed = UUID(resolution.resolution_id)
        except (ValueError, AttributeError) as exc:
            raise ExecutorValidationError("Resolver ID is invalid.") from exc
        if parsed.version != 4:
            raise ExecutorValidationError("Resolver ID is invalid.")
