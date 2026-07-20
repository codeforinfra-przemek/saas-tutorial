"""Build a safe, mutable Human Research Workbench from immutable artifacts."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

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
                    proposed_values.append(
                        {
                            "id": value.normalized_value_id,
                            "display": _display_value(value),
                            "canonical_text": value.canonical_text,
                            "type": _enum_value(value.value_type),
                            "precision": _enum_value(value.precision),
                            "needs_corroboration": value.needs_corroboration,
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
                            }
                        )
                pipeline_status = _enum_value(normalized_field.status)
                checker_status = _enum_value(normalized_field.checker_status)
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
    FranchiseResearchEvent.objects.create(
        workspace=workspace,
        event_type="workspace_created",
        message="Utworzono przestrzeń Human Review z kompletnej linii artefaktów.",
        metadata={
            "normalized_reference": str(normalized_path),
            "fields": len(rows),
        },
        actor=created_by,
    )
    return workspace, True
