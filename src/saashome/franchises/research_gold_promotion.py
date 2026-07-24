"""Safely stage benchmark Gold values in an existing Human Review workspace.

Gold remains a benchmark artifact.  Promotion never creates a publication,
never changes a human decision and never mutates an immutable finalization.
It only adds traceable proposals or gap suggestions to a mutable Workbench.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.db import transaction

from .models import (
    Franchise,
    FranchiseResearchEvent,
    FranchiseResearchReviewField,
    FranchiseResearchWorkspace,
)
from .research_benchmark import (
    ResearchBenchmarkError,
    benchmark_gold_brand,
    benchmark_paths,
)
from .research_fields import field_metadata


class GoldPromotionError(ValueError):
    """Raised when Gold cannot be staged without weakening provenance."""


@dataclass(frozen=True)
class GoldPromotionRow:
    gold: object
    policy: object
    review_field: FranchiseResearchReviewField | None
    profile_metadata: object
    selectable: bool
    selected_by_default: bool
    state: str
    state_label: str


def _gold_sha256() -> str:
    path = benchmark_paths()["gold"]
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise GoldPromotionError(f"Nie można odczytać Gold Setu: {path}") from exc


def _franchise(slug: str) -> Franchise:
    franchise = Franchise.objects.filter(slug=slug).first()
    if franchise is None:
        raise GoldPromotionError(
            "Marka z Gold Setu nie ma jeszcze rekordu franczyzy w katalogu."
        )
    return franchise


def gold_promotion_workspaces(slug: str):
    """Return mutable L1 workspaces; finalized releases are intentionally absent."""

    franchise = _franchise(slug)
    return (
        franchise.research_workspaces.filter(
            finalization__isnull=True,
            profile_id__startswith="PL:L1",
        )
        .exclude(
            status__in=[
                FranchiseResearchWorkspace.STATUS_APPROVED,
                FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS,
                FranchiseResearchWorkspace.STATUS_REJECTED,
            ]
        )
        .order_by("-updated_at", "-id")
    )


def _workspace(slug: str, workspace_id=None) -> FranchiseResearchWorkspace:
    candidates = gold_promotion_workspaces(slug)
    workspace = (
        candidates.filter(workspace_id=workspace_id).first()
        if workspace_id
        else candidates.first()
    )
    if workspace is None:
        raise GoldPromotionError(
            "Brak niezakończonego Workbencha PL:L1 dla tej marki. "
            "Najpierw uruchom albo otwórz run PL:L1."
        )
    return workspace


def gold_promotion_preview(slug: str, workspace_id=None) -> dict:
    """Build a deterministic, side-effect-free preview for one Gold brand."""

    try:
        gold_context = benchmark_gold_brand(slug)
    except ResearchBenchmarkError as exc:
        raise GoldPromotionError(str(exc)) from exc
    workspace = _workspace(slug, workspace_id)
    review_fields = list(workspace.review_fields.order_by("sort_order", "id"))
    by_target: dict[str, list[FranchiseResearchReviewField]] = {}
    for review_field in review_fields:
        by_target.setdefault(review_field.target_field, []).append(review_field)

    rows: list[GoldPromotionRow] = []
    counts = {
        "found": 0,
        "not_public": 0,
        "not_applicable": 0,
        "pending": 0,
        "ready": 0,
        "comparison_ready": 0,
        "conflict": 0,
        "missing_workbench_field": 0,
        "already_imported": 0,
    }
    for item in gold_context["field_rows"]:
        gold = item["gold"]
        policy = item["policy"]
        counts[gold.status] += 1
        matches = by_target.get(gold.target_field, [])
        review_field = matches[0] if len(matches) == 1 else None
        if gold.status == "pending":
            state, state_label = "pending_gold", "Gold nieukończony"
        elif not matches:
            state, state_label = "missing_workbench_field", "Brak pola w Workbenchu"
            counts["missing_workbench_field"] += 1
        elif len(matches) > 1:
            state, state_label = "conflict", "Niejednoznaczne pole w Workbenchu"
            counts["conflict"] += 1
        elif review_field.decision != FranchiseResearchReviewField.DECISION_PENDING:
            state, state_label = "conflict", "Pole ma już decyzję człowieka"
            counts["conflict"] += 1
        else:
            source_id = _gold_source_id(slug, gold.target_field)
            if source_id in review_field.source_ids:
                state, state_label = "already_imported", "Już przeniesiono"
                counts["already_imported"] += 1
            elif review_field.proposed_values:
                state = "comparison_ready"
                state_label = "Dodaj Gold do porównania"
                counts["comparison_ready"] += 1
            else:
                state, state_label = "ready", "Gotowe do przeniesienia"
                counts["ready"] += 1
        selectable = state in {"ready", "comparison_ready"}
        rows.append(
            GoldPromotionRow(
                gold=gold,
                policy=policy,
                review_field=review_field,
                profile_metadata=field_metadata(gold.target_field),
                selectable=selectable,
                selected_by_default=selectable,
                state=state,
                state_label=state_label,
            )
        )
    return {
        **gold_context,
        "workspace": workspace,
        "workspaces": list(gold_promotion_workspaces(slug)),
        "gold_sha256": _gold_sha256(),
        "promotion_rows": rows,
        "promotion_counts": counts,
    }


def _gold_source_id(slug: str, target_field: str) -> str:
    digest = hashlib.sha256(
        f"{slug}\x1f{target_field}".encode("utf-8")
    ).hexdigest()[:16]
    return f"benchmark-gold-{digest}"


def _iso(value) -> str:
    return value.isoformat() if value is not None else ""


@transaction.atomic
def promote_gold_to_workspace(
    slug: str,
    *,
    workspace_id,
    selected_field_ids: list[int],
    expected_gold_sha256: str,
    actor=None,
) -> dict:
    """Stage selected Gold rows while preserving every human/pipeline decision."""

    if _gold_sha256() != expected_gold_sha256:
        raise GoldPromotionError(
            "Gold Set zmienił się od czasu wyświetlenia podglądu. "
            "Odśwież podgląd przed przeniesieniem."
        )
    if not selected_field_ids:
        raise GoldPromotionError("Wybierz co najmniej jedno gotowe pole.")

    preview = gold_promotion_preview(slug, workspace_id)
    workspace = FranchiseResearchWorkspace.objects.select_for_update().get(
        pk=preview["workspace"].pk
    )
    if workspace.is_finalized:
        raise GoldPromotionError("Nie można zmieniać zamrożonego Workbencha.")

    selected_ids = set(selected_field_ids)
    available = {
        row.review_field.pk: row
        for row in preview["promotion_rows"]
        if row.selectable and row.review_field is not None
    }
    if not selected_ids.issubset(available):
        raise GoldPromotionError(
            "Wybrano pole, którego nie można już bezpiecznie przenieść. "
            "Odśwież podgląd."
        )

    artifact_hash = preview["gold_sha256"]
    imported = {"found": 0, "not_public": 0, "not_applicable": 0}
    imported_targets: list[str] = []
    for field_id in sorted(selected_ids):
        row = available[field_id]
        gold = row.gold
        field = FranchiseResearchReviewField.objects.select_for_update().get(
            pk=field_id,
            workspace=workspace,
        )
        if (
            field.decision != FranchiseResearchReviewField.DECISION_PENDING
            or _gold_source_id(slug, gold.target_field) in field.source_ids
        ):
            raise GoldPromotionError(
                f"Pole {gold.target_field} zmieniło się od czasu podglądu."
            )

        source_id = _gold_source_id(slug, gold.target_field)
        evidence = {
            "source_id": source_id,
            "source_title": "Benchmark Gold PL:L1 — źródło referencyjne",
            "url": gold.source_url,
            "source_type": gold.source_type,
            "observed_at": _iso(gold.observed_at),
            "valid_as_of": _iso(gold.valid_as_of),
            "provenance": "benchmark_gold_ai_proxy",
            "artifact_sha256": artifact_hash,
            "benchmark_status": gold.status,
            "benchmark_value": gold.canonical_value,
            "note": (
                "Wartość przeniesiona z niezależnego artefaktu benchmarkowego AI "
                "proxy; wymaga decyzji człowieka przed publikacją."
            ),
        }
        note = (
            "[benchmark_gold_ai_proxy] "
            f"status={gold.status}; artifact_sha256={artifact_hash}; "
            f"observed_at={_iso(gold.observed_at) or 'brak'}; "
            f"valid_as_of={_iso(gold.valid_as_of) or 'brak'}; "
            f"value={gold.canonical_value or 'brak'}; "
            f"notes={gold.notes or 'brak'}"
        )
        field.evidence = [*field.evidence, evidence]
        field.source_ids = [*field.source_ids, source_id]
        field.notes = [*field.notes, note]
        field.checker_status = "needs_review"
        if gold.status == "found" and not field.proposed_values:
            field.proposed_values = [
                {
                    "id": f"gold:{slug}:{gold.target_field}",
                    "display": gold.canonical_value,
                    "canonical_text": gold.canonical_value,
                    "value_type": row.policy.value_type,
                    "needs_corroboration": True,
                    "source_ids": [source_id],
                    "provenance": "benchmark_gold_ai_proxy",
                    "artifact_sha256": artifact_hash,
                }
            ]
            field.pipeline_status = "gold_proposal"
        elif gold.status == "not_public" and not field.proposed_values:
            field.pipeline_status = "gold_gap_suggested"
        elif gold.status == "not_applicable" and not field.proposed_values:
            field.pipeline_status = "gold_na_suggested"
        elif gold.status not in {"found", "not_public", "not_applicable"}:
            raise GoldPromotionError(f"Pole {gold.target_field} nie jest ukończone.")
        field.save(
            update_fields=[
                "proposed_values",
                "evidence",
                "source_ids",
                "notes",
                "pipeline_status",
                "checker_status",
                "updated_at",
            ]
        )
        imported[gold.status] += 1
        imported_targets.append(gold.target_field)

    FranchiseResearchEvent.objects.create(
        workspace=workspace,
        event_type="benchmark_gold_staged",
        message=(
            f"Przeniesiono {len(imported_targets)} pól Gold do Human Review; "
            "żadne pole nie zostało automatycznie zatwierdzone."
        ),
        metadata={
            "provenance": "benchmark_gold_ai_proxy",
            "gold_artifact": str(benchmark_paths()["gold"]),
            "gold_sha256": artifact_hash,
            "selected_field_ids": sorted(selected_ids),
            "target_fields": imported_targets,
            "status_counts": imported,
            "auto_approved": False,
        },
        actor=actor,
    )
    return {
        "workspace": workspace,
        "imported": len(imported_targets),
        "status_counts": imported,
        "target_fields": imported_targets,
        "gold_sha256": artifact_hash,
    }
