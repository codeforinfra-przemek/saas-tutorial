"""Conservative, auditable auto-review for the public PL:L1 contract."""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
import re
import sys
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.utils import timezone

REPOSITORY_ROOT = settings.BASE_DIR.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

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


def _normalized_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _semantic_shape_accepts(field, *, franchise_name: str) -> tuple[bool, str]:
    proposal = field.proposed_values[0]
    value = str(
        proposal.get("canonical_text")
        or proposal.get("display")
        or ""
    ).strip()
    if not value or any(character in value for character in ("\x00", "\r")):
        return False, "invalid_public_text"
    if " | " in value or len(value.splitlines()) > 4:
        return False, "uncompacted_public_text"
    target = field.target_field
    if target in {"websites.official", "websites.franchise_offer"}:
        return (
            (True, "")
            if _valid_public_url(value)
            else (False, "invalid_public_url")
        )
    if target == "brand.name":
        if not 2 <= len(value) <= 160:
            return False, "invalid_brand_name_length"
        if _normalized_label(value) != _normalized_label(franchise_name):
            return False, "brand_name_mismatch"
        return True, ""
    maximum = {
        "brand.public_summary": 400,
        "contact.generic_business_route": 240,
        "offer.unit_formats": 240,
        "candidate.premises_requirements": 400,
        "support.training_program": 400,
    }.get(target, 400)
    if not 3 <= len(value) <= maximum:
        return False, "invalid_public_text_length"
    if len(re.findall(r"[.!?]+(?:\s|$)", value)) > 3:
        return False, "too_many_public_sentences"
    return True, ""


def _field_policy_accepts(
    field,
    policy,
    *,
    now,
    franchise_name: str,
) -> tuple[bool, str]:
    if field.target_field not in L1_AUTO_REVIEW_SAFE_FIELDS:
        return False, "human_review_required_by_field_policy"
    source_metadata_value = (
        field.pipeline_status == "derived_source_metadata"
        and len(field.proposed_values) == 1
        and field.proposed_values[0].get("provenance")
        == "official_search_source_metadata"
        and field.target_field
        in {"brand.name", "websites.official", "websites.franchise_offer"}
    )
    risk_based_low_risk = (
        field.pipeline_status == "normalized"
        and field.checker_status == "not_reviewed"
        and len(field.proposed_values) == 1
        and field.proposed_values[0].get("provenance")
        == "risk_based_low_risk_normalized"
    )
    if field.pipeline_status not in {
        "normalized",
        "derived",
        "needs_review",
        "derived_source_metadata",
    }:
        return False, f"pipeline_status:{field.pipeline_status or 'missing'}"
    if (
        field.checker_status not in {"verified", "partial"}
        and not source_metadata_value
        and not risk_based_low_risk
    ):
        return False, f"checker_status:{field.checker_status or 'missing'}"
    if len(field.proposed_values) != 1 or not field.proposed_display:
        return False, "requires_exactly_one_nonempty_value"
    proposal = field.proposed_values[0]
    if proposal.get("needs_corroboration"):
        return False, "normalizer_requires_corroboration"
    semantic_shape_ok, semantic_shape_reason = _semantic_shape_accepts(
        field,
        franchise_name=franchise_name,
    )
    if not semantic_shape_ok:
        return False, semantic_shape_reason

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
            franchise_name=workspace.franchise.name,
        )
        if allowed:
            field.decision = (
                FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED
            )
            field.reviewer_note = (
                f"Automatycznie zaakceptowano przez "
                f"{L1_AUTO_REVIEW_POLICY_VERSION}: pojedyncza wartość, brak "
                "odrzucenia przez Checker, świeże źródło dozwolonego typu."
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
