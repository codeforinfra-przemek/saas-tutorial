"""Build a safe, mutable Human Research Workbench from immutable artifacts."""

from __future__ import annotations

import re
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.db import transaction


REPOSITORY_ROOT = settings.BASE_DIR.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from datacollector.storage.json_store import (  # noqa: E402
    load_checker_results,
    load_extraction_results,
    load_normalizer_results,
    load_research_plan,
    load_search_results,
)

from .models import (  # noqa: E402
    Franchise,
    FranchiseResearchEvent,
    FranchiseResearchReviewField,
    FranchiseResearchWorkspace,
)


class ResearchWorkbenchError(ValueError):
    """Raised before inconsistent lineage reaches editorial staging."""


def _resolved(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    cwd_candidate = (Path.cwd() / candidate).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    repository_candidate = (REPOSITORY_ROOT / candidate).resolve()
    if repository_candidate.exists():
        return repository_candidate
    return cwd_candidate


def _enum_value(value) -> str:
    return str(getattr(value, "value", value))


def _normalized_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _official_url_source(sources, target_field: str):
    """Choose one provider-observed official URL according to its public role."""

    candidates = [
        source
        for source in sources
        if _enum_value(source.source_type) == "official"
        and getattr(source, "provider_observed", False)
    ]
    if not candidates:
        return None, ""

    franchise_tokens = ("franczy", "franchis", "oferta")

    def url_parts(source):
        parts = urlsplit(source.canonical_url)
        return (
            parts,
            (parts.hostname or "").casefold(),
            (parts.path or "/").casefold(),
        )

    ranked = []
    if target_field == "websites.official":
        for source in candidates:
            parts, host, path = url_parts(source)
            ranked.append(
                (
                    any(token in host for token in franchise_tokens),
                    path not in {"", "/"},
                    len(source.canonical_url),
                    source.source_id,
                    source,
                    urlunsplit((parts.scheme, parts.netloc, "/", "", "")),
                )
            )
    else:
        for source in candidates:
            _parts, host, path = url_parts(source)
            hostname_is_franchise = any(
                token in host for token in franchise_tokens
            )
            path_is_franchise = any(token in path for token in franchise_tokens)
            if not hostname_is_franchise and not path_is_franchise:
                continue
            ranked.append(
                (
                    not hostname_is_franchise,
                    not path_is_franchise,
                    path not in {"", "/"} if hostname_is_franchise else False,
                    len(source.canonical_url),
                    source.source_id,
                    source,
                    source.canonical_url,
                )
            )
    if not ranked:
        return None, ""
    ranked.sort(key=lambda item: item[:-2])
    selected = ranked[0]
    return selected[-2], selected[-1]


def _display_value(value) -> str:
    value_type = _enum_value(value.value_type)
    if value_type in {"integer", "decimal", "money", "percentage"}:
        low = "" if value.number_min is None else str(value.number_min)
        high = "" if value.number_max is None else str(value.number_max)
        rendered = low if not high or high == low else f"{low} – {high}"
        suffix = value.currency or value.unit or ("%" if value_type == "percentage" else "")
        return f"{rendered} {suffix}".strip()
    if value_type == "boolean":
        return "Tak" if value.boolean_value else "Nie"
    if value_type == "date" and value.date_value:
        return value.date_value.isoformat()
    return value.canonical_text


def _usage_summary(artifact) -> dict:
    input_tokens = output_tokens = reasoning_tokens = tool_calls = 0
    cost = Decimal("0")
    for usage in getattr(artifact, "agent_usage", []) or []:
        tokens = usage.tokens
        input_tokens += tokens.input_tokens
        output_tokens += tokens.output_tokens
        reasoning_tokens += tokens.reasoning_tokens
        tool_calls += sum(item.calls for item in usage.tool_usage)
        if usage.cost_estimate is not None:
            cost += usage.cost_estimate.total_estimated_cost_usd
    return {
        "api_calls": len(getattr(artifact, "agent_usage", []) or []),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": input_tokens + output_tokens,
        "tool_calls": tool_calls,
        "estimated_cost_usd": str(cost.quantize(Decimal("0.00000001"))),
    }


def _merge_usage(summaries: list[dict]) -> dict:
    total = {
        "api_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "tool_calls": 0,
    }
    cost = Decimal("0")
    for item in summaries:
        for key in total:
            total[key] += item[key]
        cost += Decimal(item["estimated_cost_usd"])
    total["estimated_cost_usd"] = str(cost.quantize(Decimal("0.00000001")))
    return total


def _load_lineage(normalized_path: Path):
    normalized, normalized_sha256 = load_normalizer_results(normalized_path)
    plan_path = _resolved(normalized.plan_reference)
    search_path = _resolved(normalized.search_reference)
    extraction_path = _resolved(normalized.extraction_reference)
    check_path = _resolved(normalized.check_reference)
    plan, plan_sha256 = load_research_plan(plan_path)
    search, search_sha256 = load_search_results(search_path)
    extraction, extraction_sha256 = load_extraction_results(extraction_path)
    checker, check_sha256 = load_checker_results(check_path)
    for actual, expected, label in (
        (plan_sha256, normalized.plan_sha256, "Planner"),
        (search_sha256, normalized.search_sha256, "Searcher"),
        (extraction_sha256, normalized.extraction_sha256, "Extractor"),
        (check_sha256, normalized.check_sha256, "Checker"),
    ):
        if actual != expected:
            raise ResearchWorkbenchError(
                f"{label} artifact does not match the Normalizer lineage."
            )
    return normalized, normalized_sha256, plan, search, extraction, checker


def _stage_summary(plan, search, extraction, checker, normalized) -> tuple[list[dict], dict]:
    stage_inputs = [
        (
            "plan",
            "Plan",
            "complete",
            f"{len(plan.tasks)} zadań badawczych",
            _usage_summary(plan),
        ),
        (
            "search",
            "Źródła",
            "complete" if search.sources else "attention",
            f"{len(search.sources)} źródeł",
            _usage_summary(search),
        ),
        (
            "extract",
            "Ekstrakcja",
            "complete" if extraction.claims else "attention",
            f"{len(extraction.claims)} twierdzeń",
            _usage_summary(extraction),
        ),
        (
            "check",
            "Kontrola jakości",
            "complete" if checker.passed else "attention",
            f"wynik {checker.quality_score}/{checker.quality_threshold}",
            _usage_summary(checker),
        ),
        (
            "normalize",
            "Normalizacja",
            "complete" if normalized.normalized_values else "attention",
            f"{len(normalized.normalized_values)} wartości",
            _usage_summary(normalized),
        ),
    ]
    stages = [
        {
            "key": key,
            "label": label,
            "status": status,
            "summary": summary,
            "usage": usage,
        }
        for key, label, status, summary, usage in stage_inputs
    ]
    stages.extend(
        [
            {
                "key": "review",
                "label": "Human Review",
                "status": "current",
                "summary": "decyzje redakcyjne",
                "usage": None,
            },
            {
                "key": "import",
                "label": "Publikacja",
                "status": "pending",
                "summary": "po zatwierdzeniu",
                "usage": None,
            },
        ]
    )
    return stages, _merge_usage([item[4] for item in stage_inputs])


@transaction.atomic
def create_research_workspace(
    normalized_reference: str | Path,
    *,
    franchise_slug: str,
    created_by=None,
) -> tuple[FranchiseResearchWorkspace, bool]:
    """Create an idempotent review workspace from an exact Normalizer lineage."""

    normalized_path = _resolved(normalized_reference)
    normalized, normalized_sha256, plan, search, extraction, checker = _load_lineage(
        normalized_path
    )
    franchise = Franchise.objects.filter(slug=franchise_slug).first()
    if franchise is None:
        raise ResearchWorkbenchError(
            f"Franchise {franchise_slug!r} does not exist. Create its directory entry first."
        )
    existing = FranchiseResearchWorkspace.objects.filter(
        normalization_id=normalized.normalization_id
    ).first()
    if existing:
        if existing.normalized_sha256 != normalized_sha256:
            raise ResearchWorkbenchError(
                "A workspace with this normalization_id has different bytes."
            )
        return existing, False

    stages, cost_summary = _stage_summary(
        plan, search, extraction, checker, normalized
    )
    profile_snapshot = getattr(plan, "profile_snapshot", None)
    profile_id = (
        getattr(profile_snapshot, "profile_id", "")
        or getattr(plan.planner_input, "profile_id", "")
        or ""
    )
    evaluated_task_ids = set(checker.selected_task_ids)
    planned_fields = sum(
        len(dict.fromkeys(task.fields_to_collect or task.target_fields))
        for task in plan.tasks
    )
    workspace = FranchiseResearchWorkspace.objects.create(
        franchise=franchise,
        normalization_id=normalized.normalization_id,
        plan_run_id=normalized.plan_run_id,
        target_country=normalized.target_country,
        depth=_enum_value(normalized.depth),
        profile_id=profile_id,
        iteration=normalized.iteration,
        normalized_reference=str(normalized_path),
        normalized_sha256=normalized_sha256,
        quality_score=normalized.input_quality_score,
        quality_threshold=normalized.input_quality_threshold,
        checker_passed=normalized.input_checker_passed,
        scope_complete=normalized.input_scope_complete,
        planned_tasks=len(plan.tasks),
        evaluated_tasks=len(evaluated_task_ids),
        planned_fields=planned_fields,
        source_count=len(search.sources),
        claim_count=len(extraction.claims),
        normalized_values_count=len(normalized.normalized_values),
        stage_summary=stages,
        cost_summary=cost_summary,
        warnings=normalized.warnings,
        created_by=created_by,
    )

    values_by_id = {
        item.normalized_value_id: item for item in normalized.normalized_values
    }
    fields_by_key = {
        (item.task_id, item.target_field): item for item in normalized.field_results
    }
    sources_by_id = {item.source_id: item for item in search.sources}
    citations_by_id = {item.citation_id: item for item in extraction.citations}
    checker_decisions_by_id = {
        item.claim_id: item for item in checker.claim_decisions
    }
    grounded_review_by_key = {}
    if _enum_value(getattr(checker, "checker_mode", "")) == "risk_based":
        for claim in extraction.claims:
            decision = checker_decisions_by_id.get(claim.claim_id)
            if (
                decision is not None
                and _enum_value(decision.verdict)
                in {"not_reviewed", "needs_review"}
            ):
                grounded_review_by_key.setdefault(
                    (claim.task_id, claim.target_field),
                    [],
                ).append((claim, _enum_value(decision.verdict)))
    rows = []
    sort_order = 0
    for task in plan.tasks:
        target_fields = list(dict.fromkeys(task.fields_to_collect or task.target_fields))
        for target_field in target_fields:
            sort_order += 1
            normalized_field = fields_by_key.get((task.task_id, target_field))
            proposed_values = []
            evidence = []
            seen_citations = set()
            if normalized_field:
                for value_id in normalized_field.normalized_value_ids:
                    value = values_by_id[value_id]
                    value_decisions = [
                        checker_decisions_by_id.get(claim_id)
                        for claim_id in value.claim_ids
                    ]
                    value_is_risk_based_low_risk = (
                        _enum_value(getattr(checker, "checker_mode", ""))
                        == "risk_based"
                        and bool(value_decisions)
                        and all(
                            decision is not None
                            and _enum_value(decision.verdict) == "not_reviewed"
                            for decision in value_decisions
                        )
                    )
                    proposed_values.append(
                        {
                            "id": value.normalized_value_id,
                            "display": _display_value(value),
                            "canonical_text": value.canonical_text,
                            "type": _enum_value(value.value_type),
                            "precision": _enum_value(value.precision),
                            "needs_corroboration": value.needs_corroboration,
                            **(
                                {
                                    "provenance": (
                                        "risk_based_low_risk_normalized"
                                    )
                                }
                                if value_is_risk_based_low_risk
                                else {}
                            ),
                        }
                    )
                    for citation_id in value.citation_ids:
                        if citation_id in seen_citations:
                            continue
                        seen_citations.add(citation_id)
                        citation = citations_by_id.get(citation_id)
                        if citation is None:
                            continue
                        source = sources_by_id.get(citation.source_id)
                        evidence.append(
                            {
                                "citation_id": citation_id,
                                "quote": citation.quote,
                                "locator": citation.locator,
                                "source_id": citation.source_id,
                                "source_title": getattr(source, "title", "") or "Źródło",
                                "url": getattr(source, "canonical_url", "") or "",
                                "source_type": _enum_value(
                                    getattr(source, "source_type", "")
                                ),
                                "discovered_at": (
                                    source.discovered_at.isoformat()
                                    if source is not None
                                    and getattr(source, "discovered_at", None)
                                    else ""
                                ),
                            }
                        )
                pipeline_status = _enum_value(normalized_field.status)
                checker_status = _enum_value(normalized_field.checker_status)
                if (
                    proposed_values
                    and all(
                        item.get("provenance")
                        == "risk_based_low_risk_normalized"
                        for item in proposed_values
                    )
                    and checker_status in {"", "missing", "not_reviewed"}
                ):
                    checker_status = "not_reviewed"
                notes = normalized_field.notes
                source_ids = normalized_field.source_ids
                normalized_field_id = normalized_field.normalized_field_id
            else:
                pipeline_status = (
                    "missing" if task.task_id in evaluated_task_ids else "not_evaluated"
                )
                checker_status = ""
                notes = []
                source_ids = []
                normalized_field_id = ""
            # A provider-observed official source title can validate the exact
            # directory label without asking a model to restate the brand.
            if not proposed_values and target_field == "brand.name":
                expected_name = franchise.name.strip()
                expected_key = _normalized_label(expected_name)
                official_identity_sources = [
                    source
                    for source in search.sources
                    if _enum_value(source.source_type) == "official"
                    and getattr(source, "provider_observed", False)
                    and task.task_id in source.task_ids
                    and expected_key
                    and expected_key in _normalized_label(source.title or "")
                ]
                selected_source = (official_identity_sources or [None])[0]
                if selected_source is not None:
                    proposed_values = [
                        {
                            "id": f"source-brand-{selected_source.source_id}",
                            "display": expected_name,
                            "canonical_text": expected_name,
                            "type": "text",
                            "precision": "exact",
                            "needs_corroboration": False,
                            "provenance": "official_search_source_metadata",
                        }
                    ]
                    evidence = [
                        {
                            "citation_id": "",
                            "quote": selected_source.title,
                            "locator": "provider-observed official source title",
                            "source_id": selected_source.source_id,
                            "source_title": selected_source.title,
                            "url": selected_source.canonical_url,
                            "source_type": "official",
                            "discovered_at": selected_source.discovered_at.isoformat(),
                            "provenance": "official_search_source_metadata",
                        }
                    ]
                    pipeline_status = "derived_source_metadata"
                    checker_status = "partial"
                    source_ids = [selected_source.source_id]
                    notes = [
                        *notes,
                        "Brand label matched deterministically in an official Searcher title.",
                    ]
            # Website URLs are already typed, canonical Searcher metadata. When
            # Searcher classified a source as official, copying that URL into
            # the review draft is safer and cheaper than asking Normalizer to
            # restate it as a semantic claim.
            if (
                (not proposed_values or pipeline_status == "multiple_values")
                and target_field in {"websites.official", "websites.franchise_offer"}
            ):
                official_sources = [
                    source
                    for source in search.sources
                    if _enum_value(source.source_type) == "official"
                    and getattr(source, "provider_observed", False)
                    and task.task_id in source.task_ids
                ]
                selected_source, derived_url = _official_url_source(
                    official_sources,
                    target_field,
                )
                if selected_source and derived_url:
                    proposed_values = [
                        {
                            "id": f"source-url-{selected_source.source_id}",
                            "display": derived_url,
                            "canonical_text": derived_url,
                            "type": "url",
                            "precision": "exact",
                            "needs_corroboration": False,
                            "provenance": "official_search_source_metadata",
                        }
                    ]
                    evidence = [
                        {
                            "citation_id": "",
                            "quote": "",
                            "locator": "canonical Searcher source URL",
                            "source_id": selected_source.source_id,
                            "source_title": selected_source.title or "Oficjalna strona",
                            "url": selected_source.canonical_url,
                            "source_type": "official",
                            "discovered_at": selected_source.discovered_at.isoformat(),
                            "provenance": "official_search_source_metadata",
                        }
                    ]
                    pipeline_status = "derived_source_metadata"
                    source_ids = [selected_source.source_id]
                    notes = [
                        *notes,
                        "URL derived deterministically from an official Searcher source.",
                    ]
            # Risk-based Checker deliberately spends semantic-review tokens only
            # on high-risk fields. Exact quote-grounded low-risk Extractor claims
            # must still reach Human Review, but remain visibly unreviewed and
            # cannot become public without an explicit human decision.
            grounded_review_claims = grounded_review_by_key.get(
                (task.task_id, target_field),
                [],
            )
            if not proposed_values and grounded_review_claims:
                raw_source_ids = []
                verdicts = set()
                for claim, verdict in grounded_review_claims:
                    verdicts.add(verdict)
                    provenance = (
                        "extractor_grounded_unreviewed"
                        if verdict == "not_reviewed"
                        else "extractor_grounded_needs_review"
                    )
                    proposed_values.append(
                        {
                            "id": claim.claim_id,
                            "display": claim.value_text,
                            "canonical_text": claim.value_text,
                            "type": "text",
                            "precision": "as_stated",
                            "needs_corroboration": True,
                            "provenance": provenance,
                        }
                    )
                    for citation_id in claim.citation_ids:
                        if citation_id in seen_citations:
                            continue
                        seen_citations.add(citation_id)
                        citation = citations_by_id.get(citation_id)
                        if citation is None:
                            continue
                        source = sources_by_id.get(citation.source_id)
                        raw_source_ids.append(citation.source_id)
                        evidence.append(
                            {
                                "citation_id": citation_id,
                                "quote": citation.quote,
                                "locator": citation.locator,
                                "source_id": citation.source_id,
                                "source_title": (
                                    getattr(source, "title", "") or "Źródło"
                                ),
                                "url": (
                                    getattr(source, "canonical_url", "") or ""
                                ),
                                "source_type": _enum_value(
                                    getattr(source, "source_type", "")
                                ),
                                "discovered_at": (
                                    source.discovered_at.isoformat()
                                    if source is not None
                                    and getattr(source, "discovered_at", None)
                                    else ""
                                ),
                                "provenance": provenance,
                            }
                        )
                source_ids = list(
                    dict.fromkeys([*source_ids, *raw_source_ids])
                )
                needs_semantic_review = "needs_review" in verdicts
                pipeline_status = (
                    "grounded_needs_review"
                    if needs_semantic_review
                    else "grounded_unreviewed"
                )
                checker_status = (
                    "needs_review"
                    if needs_semantic_review
                    else "not_reviewed"
                )
                notes = [
                    *notes,
                    (
                        "Quote-grounded Extractor proposal retained for Human "
                        "Review; it is not eligible for automatic publication."
                    ),
                ]
            rows.append(
                FranchiseResearchReviewField(
                    workspace=workspace,
                    normalized_field_id=normalized_field_id,
                    task_id=task.task_id,
                    task_title=task.title,
                    target_field=target_field,
                    requirement=_enum_value(task.requirement),
                    priority=_enum_value(task.priority),
                    pipeline_status=pipeline_status,
                    checker_status=checker_status,
                    proposed_values=proposed_values,
                    evidence=evidence,
                    source_ids=source_ids,
                    notes=notes,
                    sort_order=sort_order,
                )
            )
    FranchiseResearchReviewField.objects.bulk_create(rows)
    # Reuse decisions conservatively across releases. Identical AI proposals
    # retain an earlier human acceptance. A human-entered correction is only
    # suggested in the new draft and deliberately requires a fresh click.
    previous_workspace = (
        FranchiseResearchWorkspace.objects.filter(
            franchise=franchise,
            finalization__isnull=False,
        )
        .exclude(pk=workspace.pk)
        .order_by("-finalization__finalized_at", "-id")
        .first()
    )
    carried_accepted = 0
    carried_suggestions = 0
    if previous_workspace is not None:
        previous_by_key = {
            (item.task_id, item.target_field): item
            for item in previous_workspace.review_fields.all()
        }
        current_fields = list(workspace.review_fields.all())
        updates = []
        for field in current_fields:
            previous = previous_by_key.get((field.task_id, field.target_field))
            if previous is None:
                continue
            if previous.decision == FranchiseResearchReviewField.DECISION_ACCEPTED:
                if previous.proposed_values == field.proposed_values and field.effective_value:
                    field.decision = FranchiseResearchReviewField.DECISION_ACCEPTED
                    field.decided_by = previous.decided_by
                    field.decided_at = previous.decided_at
                    field.reviewer_note = previous.reviewer_note
                    field.inherited_from = previous
                    carried_accepted += 1
                    updates.append(field)
            elif (
                previous.decision
                == FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED
                and previous.reviewer_value.strip()
            ):
                field.reviewer_value = previous.reviewer_value
                field.reviewer_note = (
                    f"Propozycja z poprzedniej wersji. {previous.reviewer_note}"
                ).strip()
                field.inherited_from = previous
                carried_suggestions += 1
                updates.append(field)
        if updates:
            FranchiseResearchReviewField.objects.bulk_update(
                updates,
                [
                    "decision",
                    "reviewer_value",
                    "reviewer_note",
                    "decided_by",
                    "decided_at",
                    "inherited_from",
                ],
            )
    FranchiseResearchEvent.objects.create(
        workspace=workspace,
        event_type="workspace_created",
        message="Utworzono przestrzeń Human Review z kompletnej linii artefaktów.",
        metadata={
            "normalized_reference": str(normalized_path),
            "fields": len(rows),
            "carried_accepted": carried_accepted,
            "carried_human_suggestions": carried_suggestions,
        },
        actor=created_by,
    )
    return workspace, True
