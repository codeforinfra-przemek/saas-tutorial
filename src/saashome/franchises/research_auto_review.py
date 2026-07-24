"""Conservative, auditable auto-review for the public PL:L1 contract."""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone

from datacollector.benchmark import field_policy_map

from .models import (
    FranchiseResearchEvent,
    FranchiseResearchReviewField,
    FranchiseResearchWorkspace,
)
from .research_fields import (
    L1_AUTO_REVIEW_POLICY_VERSION,
    L1_AUTO_REVIEW_SAFE_FIELDS,
    L1_PUBLIC_FIELD_ORDER,
)


class ResearchAutoReviewError(ValueError):
    """Raised when a workspace cannot be processed by the L1 policy."""


MISSING_STATUSES = {"missing", "not_accessible", "not_applicable"}


def _parse_datetime(value: str):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def _valid_public_url(value: str) -> bool:
    parsed = urlparse(value or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _field_policy_accepts(field, policy, *, now) -> tuple[bool, str]:
    if field.target_field not in L1_AUTO_REVIEW_SAFE_FIELDS:
        return False, "human_review_required_by_field_policy"
    if field.pipeline_status not in {"normalized", "derived"}:
        return False, f"pipeline_status:{field.pipeline_status or 'missing'}"
    if field.checker_status != "verified":
        return False, f"checker_status:{field.checker_status or 'missing'}"
    if len(field.proposed_values) != 1 or not field.proposed_display:
        return False, "requires_exactly_one_nonempty_value"
    proposal = field.proposed_values[0]
    if proposal.get("needs_corroboration"):
        return False, "normalizer_requires_corroboration"

    acceptable = set(policy.accepted_source_types)
    evidence_by_url = {}
    for row in field.evidence:
        url = str(row.get("url") or "").strip()
        source_type = str(row.get("source_type") or "").strip()
        if not _valid_public_url(url) or source_type not in acceptable:
            continue
        observed_at = _parse_datetime(str(row.get("discovered_at") or ""))
        if observed_at is None:
            continue
        if policy.max_age_days is not None:
            age_days = (now - observed_at).days
            if age_days < 0 or age_days > policy.max_age_days:
                continue
        evidence_by_url[url] = row
    if len(evidence_by_url) < policy.minimum_sources:
        return False, "insufficient_typed_fresh_sources"
    return True, ""


@transaction.atomic
def auto_review_l1_workspace(
    workspace: FranchiseResearchWorkspace,
    *,
    actor=None,
) -> dict:
    """Apply only versioned deterministic decisions and sign as system policy."""

    workspace = FranchiseResearchWorkspace.objects.select_for_update().get(
        pk=workspace.pk
    )
    if workspace.is_finalized:
        raise ResearchAutoReviewError("Sfinalizowany Workbench jest niezmienny.")
    if workspace.profile_id not in {"PL:L1", "PL:L1:v2"}:
        raise ResearchAutoReviewError("Auto-review jest dostępny tylko dla PL:L1:v2.")

    policies = field_policy_map("PL:L1:v2")
    if set(policies) != set(L1_PUBLIC_FIELD_ORDER):
        raise ResearchAutoReviewError(
            "Kontrakt 20 pól L1 różni się od polityki benchmarkowej."
        )
    now = timezone.now()
    accepted = []
    gaps = []
    pending = []
    reasons = {}
    updates = []
    fields = list(workspace.review_fields.order_by("sort_order", "id"))
    for field in fields:
        if field.target_field not in policies:
            continue
        if field.decision != FranchiseResearchReviewField.DECISION_PENDING:
            continue
        policy = policies[field.target_field]
        allowed, reason = _field_policy_accepts(
            field,
            policy,
            now=now,
        )
        if allowed:
            field.decision = (
                FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED
            )
            field.reviewer_note = (
                f"Automatycznie zaakceptowano przez "
                f"{L1_AUTO_REVIEW_POLICY_VERSION}: pojedyncza wartość, Checker "
                "verified, świeże źródło dozwolonego typu."
            )
            field.decided_at = now
            field.decided_by = None
            field.updated_at = now
            accepted.append(field.target_field)
            updates.append(field)
            continue
        if (
            not field.proposed_values
            and field.pipeline_status in MISSING_STATUSES
            and field.checker_status in MISSING_STATUSES
        ):
            field.decision = (
                FranchiseResearchReviewField.DECISION_DOCUMENTED_GAP
            )
            field.reviewer_note = (
                f"Automatycznie udokumentowany brak w tym runie przez "
                f"{L1_AUTO_REVIEW_POLICY_VERSION}; nie oznacza braku na rynku."
            )
            field.decided_at = now
            field.decided_by = None
            field.updated_at = now
            gaps.append(field.target_field)
            updates.append(field)
            continue
        pending.append(field.target_field)
        reasons[field.target_field] = reason

    if updates:
        FranchiseResearchReviewField.objects.bulk_update(
            updates,
            ["decision", "reviewer_note", "decided_at", "decided_by", "updated_at"],
        )
    summary = {
        "policy_version": L1_AUTO_REVIEW_POLICY_VERSION,
        "contract_fields": len(L1_PUBLIC_FIELD_ORDER),
        "policy_accepted": len(accepted),
        "documented_gaps": len(gaps),
        "pending_human_review": len(pending),
        "accepted_fields": accepted,
        "gap_fields": gaps,
        "pending_fields": pending,
        "pending_reasons": reasons,
    }
    has_publishable_values = bool(accepted)
    workspace.status = (
        FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS
        if has_publishable_values
        else FranchiseResearchWorkspace.STATUS_REVIEW
    )
    workspace.auto_reviewed = has_publishable_values
    workspace.review_policy_version = L1_AUTO_REVIEW_POLICY_VERSION
    workspace.auto_review_summary = summary
    workspace.reviewed_by = None
    workspace.reviewed_at = now if has_publishable_values else None
    workspace.reviewer_notes = (
        f"Automatyczne częściowe zatwierdzenie {L1_AUTO_REVIEW_POLICY_VERSION}. "
        "Tylko bezpieczne pola publiczne zostały dopuszczone; pola finansowe, "
        "skala sieci i niepewne propozycje nadal wymagają Human Review."
    )
    workspace.save(
        update_fields=[
            "status",
            "auto_reviewed",
            "review_policy_version",
            "auto_review_summary",
            "reviewed_by",
            "reviewed_at",
            "reviewer_notes",
            "updated_at",
        ]
    )
    FranchiseResearchEvent.objects.create(
        workspace=workspace,
        event_type="l1_auto_reviewed",
        message=(
            f"Polityka {L1_AUTO_REVIEW_POLICY_VERSION} zaakceptowała "
            f"{len(accepted)}/20 pól L1."
        ),
        metadata=summary,
        actor=actor,
    )
    return summary
