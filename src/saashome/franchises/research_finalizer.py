"""Freeze a reviewed Workbench and attach its human overlay to an import."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction


REPOSITORY_ROOT = settings.BASE_DIR.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from datacollector.agents.reviewer import HumanReviewer, render_review_html  # noqa: E402
from datacollector.schemas import HumanReviewDecision  # noqa: E402
from datacollector.storage.json_store import (  # noqa: E402
    human_review_paths_for,
    load_checker_results,
    load_extraction_results,
    load_human_review_results,
    load_normalizer_results,
    load_research_plan,
    load_search_results,
    save_human_review_results,
)

from .models import (  # noqa: E402
    FranchiseResearchArtifact,
    FranchiseResearchEditorialDecision,
    FranchiseResearchEditorialDocument,
    FranchiseResearchEvent,
    FranchiseResearchField,
    FranchiseResearchFinalization,
    FranchiseResearchImport,
    FranchiseResearchReviewField,
    FranchiseResearchWorkspace,
)
from .research_import import import_franchise_research  # noqa: E402
from .research_publication import project_approved_research  # noqa: E402


class ResearchFinalizationError(ValueError):
    """Raised before a mutable or inconsistent Workbench can be released."""


def _resolved(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    cwd_candidate = (Path.cwd() / candidate).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return (REPOSITORY_ROOT / candidate).resolve()


def _canonical_bytes(value: dict) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_immutable(path: Path, payload: bytes) -> str:
    """Create once, or prove that a prior retry produced identical bytes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = path.read_bytes()
        if existing != payload:
            raise ResearchFinalizationError(
                f"Immutable finalization artifact already exists with different bytes: {path}"
            ) from None
    else:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    return hashlib.sha256(payload).hexdigest()


def _user_label(user) -> str:
    if user is None:
        return ""
    return (user.get_full_name() or user.get_username()).strip()


def _reviewer_label(workspace: FranchiseResearchWorkspace) -> str:
    if workspace.auto_reviewed:
        return f"System / {workspace.review_policy_version}"
    return _user_label(workspace.reviewed_by)


def _load_lineage(workspace: FranchiseResearchWorkspace) -> dict:
    normalized_path = _resolved(workspace.normalized_reference)
    normalized, normalized_sha256 = load_normalizer_results(normalized_path)
    if (
        str(normalized.normalization_id) != str(workspace.normalization_id)
        or normalized_sha256 != workspace.normalized_sha256
    ):
        raise ResearchFinalizationError(
            "Normalizer ID or bytes no longer match the Workbench snapshot."
        )
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
            raise ResearchFinalizationError(
                f"{label} bytes no longer match the approved Normalizer lineage."
            )
    return {
        "normalized": normalized,
        "normalized_path": normalized_path,
        "normalized_sha256": normalized_sha256,
        "plan": plan,
        "plan_sha256": plan_sha256,
        "search": search,
        "search_sha256": search_sha256,
        "extraction": extraction,
        "extraction_sha256": extraction_sha256,
        "checker": checker,
        "check_sha256": check_sha256,
    }


def _review_decision(workspace: FranchiseResearchWorkspace) -> HumanReviewDecision:
    if workspace.status == FranchiseResearchWorkspace.STATUS_APPROVED:
        if not workspace.checker_passed or not workspace.scope_complete:
            raise ResearchFinalizationError(
                "Full approval requires a passed Checker and complete scope."
            )
        return HumanReviewDecision.APPROVED
    if workspace.status == FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS:
        return HumanReviewDecision.APPROVED_WITH_GAPS
    raise ResearchFinalizationError(
        "First approve the Workbench, with or without documented gaps."
    )


def _ensure_base_import(
    workspace: FranchiseResearchWorkspace,
    lineage: dict,
    decision: HumanReviewDecision,
) -> FranchiseResearchImport:
    existing = FranchiseResearchImport.objects.filter(
        normalization_id=workspace.normalization_id
    ).select_related("franchise").first()
    if existing is not None:
        if (
            existing.franchise_id != workspace.franchise_id
            or existing.normalized_sha256 != workspace.normalized_sha256
        ):
            raise ResearchFinalizationError(
                "Existing import does not match this Workbench franchise or bytes."
            )
        if existing.decision != decision.value:
            raise ResearchFinalizationError(
                "Existing import used a different approval level for this Normalizer."
            )
        return existing

    reviewer_name = _reviewer_label(workspace)
    output_dir = (
        lineage["normalized_path"].parent
        / "finalizations"
        / str(workspace.workspace_id)
    )
    review_path, report_path = human_review_paths_for(
        lineage["normalized"].iteration,
        decision.value,
        output_dir,
    )
    if review_path.exists() or report_path.exists():
        if not review_path.is_file() or not report_path.is_file():
            raise ResearchFinalizationError(
                "Incomplete Human Review artifact pair blocks safe retry."
            )
        review, _ = load_human_review_results(review_path)
        if (
            str(review.normalization_id) != str(workspace.normalization_id)
            or review.normalized_sha256 != workspace.normalized_sha256
            or review.decision != decision
            or review.reviewer != reviewer_name
            or Path(review.report_reference).resolve() != report_path.resolve()
        ):
            raise ResearchFinalizationError(
                "Existing Human Review artifact does not match this finalization."
            )
    else:
        review = HumanReviewer().create_review(
            lineage["plan"],
            lineage["search"],
            lineage["extraction"],
            lineage["checker"],
            lineage["normalized"],
            plan_sha256=lineage["plan_sha256"],
            search_sha256=lineage["search_sha256"],
            extraction_sha256=lineage["extraction_sha256"],
            check_sha256=lineage["check_sha256"],
            normalized_sha256=lineage["normalized_sha256"],
            normalized_reference=str(lineage["normalized_path"]),
            report_reference=str(report_path.resolve()),
            decision=decision,
            reviewer=reviewer_name,
            reviewer_notes=workspace.reviewer_notes,
            acknowledge_incomplete=(
                decision == HumanReviewDecision.APPROVED_WITH_GAPS
            ),
        )
        report = render_review_html(
            review,
            lineage["plan"],
            lineage["search"],
            lineage["extraction"],
            lineage["checker"],
            lineage["normalized"],
        )
        save_human_review_results(
            review,
            report,
            lineage["normalized_path"],
            output_dir=output_dir,
        )
    research_import, _ = import_franchise_research(
        review_path,
        franchise_slug=workspace.franchise.slug,
        allow_approved_with_gaps=(
            decision == HumanReviewDecision.APPROVED_WITH_GAPS
        ),
    )
    return research_import


def _document_snapshot(document) -> dict:
    return {
        "workbench_document_id": document.pk,
        "original_name": document.original_name,
        "document_type": document.document_type,
        "access_level": document.access_level,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "sha256": document.sha256,
        "notes": document.notes,
    }


def _field_snapshot(field) -> dict:
    document_ids = sorted(
        field.supporting_documents.values_list("id", flat=True)
    )
    return {
        "workbench_field_id": field.pk,
        "normalized_field_id": field.normalized_field_id,
        "task_id": field.task_id,
        "task_title": field.task_title,
        "target_field": field.target_field,
        "requirement": field.requirement,
        "priority": field.priority,
        "pipeline_status": field.pipeline_status,
        "checker_status": field.checker_status,
        "decision": field.decision,
        "value_origin": (
            "human"
            if field.decision == FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED
            else "policy"
            if field.decision
            == FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED
            else "ai"
            if field.decision == FranchiseResearchReviewField.DECISION_ACCEPTED
            else "none"
        ),
        "effective_value": (
            field.reviewer_value.strip()
            if field.decision
            == FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED
            else field.proposed_display
            if field.decision
            in {
                FranchiseResearchReviewField.DECISION_ACCEPTED,
                FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED,
            }
            else ""
        ),
        "proposed_values": field.proposed_values,
        "evidence": field.evidence,
        "source_ids": field.source_ids,
        "reviewer_note": field.reviewer_note,
        "decided_by": _user_label(field.decided_by),
        "decided_at": field.decided_at.isoformat() if field.decided_at else None,
        "supporting_document_ids": document_ids,
    }


@transaction.atomic
def finalize_research_workspace(
    workspace: FranchiseResearchWorkspace,
    *,
    actor=None,
    active_job_id=None,
) -> tuple[FranchiseResearchFinalization, bool]:
    """Finalize and import once. Repeated calls return the exact same release."""

    # Lock only the Workbench row. Joining the nullable ``reviewed_by`` relation
    # here makes PostgreSQL reject FOR UPDATE on the nullable side of the outer
    # join. Related records are intentionally loaded with ordinary follow-up
    # queries after the row lock has been acquired.
    workspace = FranchiseResearchWorkspace.objects.select_for_update().get(
        pk=workspace.pk
    )
    try:
        finalization = workspace.finalization
    except FranchiseResearchFinalization.DoesNotExist:
        pass
    else:
        project_approved_research(finalization)
        return finalization, False

    decision = _review_decision(workspace)
    if (
        workspace.reviewed_at is None
        or (workspace.reviewed_by is None and not workspace.auto_reviewed)
    ):
        raise ResearchFinalizationError("The final Workbench decision is not signed.")
    active_jobs = workspace.jobs.filter(status__in=["queued", "running"])
    if active_job_id is not None:
        active_jobs = active_jobs.exclude(job_id=active_job_id)
    if active_jobs.exists():
        raise ResearchFinalizationError(
            "Wait for the active research job before finalizing."
        )

    fields = list(
        workspace.review_fields.select_related("decided_by")
        .prefetch_related("supporting_documents")
        .order_by("sort_order", "target_field", "id")
    )
    if not fields:
        raise ResearchFinalizationError("Workbench has no review fields to freeze.")
    documents = list(workspace.documents.order_by("id"))
    field_snapshots = [_field_snapshot(field) for field in fields]
    invalid_values = [
        item["target_field"]
        for item in field_snapshots
        if item["decision"]
        in {
            FranchiseResearchReviewField.DECISION_ACCEPTED,
            FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED,
            FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED,
        }
        and not item["effective_value"]
    ]
    if invalid_values:
        raise ResearchFinalizationError(
            "Accepted fields have no effective value: " + ", ".join(invalid_values[:5])
        )
    lineage = _load_lineage(workspace)
    research_import = _ensure_base_import(workspace, lineage, decision)
    if not research_import.profile_id and workspace.profile_id:
        research_import.profile_id = workspace.profile_id
        research_import.save(update_fields=["profile_id"])
    previous_finalization = (
        FranchiseResearchFinalization.objects.filter(
            research_import__franchise_id=workspace.franchise_id,
        )
        .exclude(workspace=workspace)
        .order_by("-release_number", "-finalized_at")
        .first()
    )
    release_number = (
        previous_finalization.release_number + 1 if previous_finalization else 1
    )
    document_snapshots = [_document_snapshot(document) for document in documents]
    state = {
        "workspace_id": str(workspace.workspace_id),
        "normalization_id": str(workspace.normalization_id),
        "normalized_sha256": workspace.normalized_sha256,
        "decision": decision.value,
        "reviewer": _reviewer_label(workspace),
        "review_policy_version": workspace.review_policy_version,
        "reviewer_notes": workspace.reviewer_notes,
        "reviewed_at": workspace.reviewed_at.isoformat(),
        "fields": field_snapshots,
        "documents": document_snapshots,
    }
    state_sha256 = hashlib.sha256(_canonical_bytes(state)).hexdigest()
    finalization_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"workbench:{workspace.workspace_id}:{workspace.normalized_sha256}:{state_sha256}",
    )
    counts = {
        name: sum(item["decision"] == decision_value for item in field_snapshots)
        for name, decision_value in (
            ("accepted", FranchiseResearchReviewField.DECISION_ACCEPTED),
            ("edited", FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED),
            (
                "policy_accepted",
                FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED,
            ),
            ("rejected", FranchiseResearchReviewField.DECISION_REJECTED),
            ("gaps", FranchiseResearchReviewField.DECISION_DOCUMENTED_GAP),
            ("pending", FranchiseResearchReviewField.DECISION_PENDING),
        )
    }
    artifact = {
        "schema_version": "1.0.0",
        "artifact_type": "workbench_finalization",
        "release_number": release_number,
        "supersedes_finalization_id": (
            str(previous_finalization.finalization_id)
            if previous_finalization
            else None
        ),
        "finalization_id": str(finalization_id),
        "created_at": workspace.reviewed_at.isoformat(),
        "franchise": {
            "id": workspace.franchise_id,
            "slug": workspace.franchise.slug,
            "name": workspace.franchise.name,
        },
        "research_import": {
            "id": research_import.pk,
            "review_id": str(research_import.review_id),
            "normalization_id": str(research_import.normalization_id),
        },
        "workspace_state_sha256": state_sha256,
        "coverage": {"fields": len(fields), "documents": len(documents), **counts},
        "state": state,
        "privacy": {
            "document_bytes_included": False,
            "storage_paths_included": False,
            "private_documents_sent_to_ai": False,
        },
    }
    artifact_bytes = _canonical_bytes(artifact)
    artifact_path = (
        lineage["normalized_path"].parent
        / "finalizations"
        / str(workspace.workspace_id)
        / "workbench-finalization.json"
    )
    artifact_sha256 = _write_immutable(artifact_path, artifact_bytes)

    finalization = FranchiseResearchFinalization.objects.create(
        finalization_id=finalization_id,
        workspace=workspace,
        research_import=research_import,
        release_number=release_number,
        supersedes=previous_finalization,
        decision=decision.value,
        reviewer=workspace.reviewed_by,
        reviewer_name=_reviewer_label(workspace),
        reviewer_notes=workspace.reviewer_notes,
        normalized_sha256=workspace.normalized_sha256,
        workspace_state_sha256=state_sha256,
        artifact_reference=str(artifact_path.resolve()),
        artifact_sha256=artifact_sha256,
        field_count=len(fields),
        accepted_count=counts["accepted"],
        edited_count=counts["edited"],
        policy_accepted_count=counts["policy_accepted"],
        rejected_count=counts["rejected"],
        gap_count=counts["gaps"],
        pending_count=counts["pending"],
        document_count=len(documents),
        finalized_at=workspace.reviewed_at,
    )
    document_records = {}
    for document, snapshot in zip(documents, document_snapshots, strict=True):
        document_records[document.pk] = FranchiseResearchEditorialDocument.objects.create(
            finalization=finalization,
            workbench_document=document,
            original_name=snapshot["original_name"],
            document_type=snapshot["document_type"],
            access_level=snapshot["access_level"],
            content_type=snapshot["content_type"],
            size_bytes=snapshot["size_bytes"],
            sha256=snapshot["sha256"],
            notes=snapshot["notes"],
        )
    imported_fields = {
        (field.task.task_id, field.target_field): field
        for field in FranchiseResearchField.objects.filter(
            task__research_import=research_import
        ).select_related("task")
    }
    for field, snapshot in zip(fields, field_snapshots, strict=True):
        editorial = FranchiseResearchEditorialDecision.objects.create(
            finalization=finalization,
            research_field=imported_fields.get((field.task_id, field.target_field)),
            task_id=field.task_id,
            task_title=field.task_title,
            target_field=field.target_field,
            requirement=field.requirement,
            priority=field.priority,
            pipeline_status=field.pipeline_status,
            checker_status=field.checker_status,
            decision=field.decision,
            value_origin=snapshot["value_origin"],
            effective_value=snapshot["effective_value"],
            proposed_values=field.proposed_values,
            evidence=field.evidence,
            source_ids=field.source_ids,
            reviewer_note=field.reviewer_note,
            decided_by_name=snapshot["decided_by"],
            decided_at=field.decided_at,
        )
        editorial.supporting_documents.set(
            document_records[document_id]
            for document_id in snapshot["supporting_document_ids"]
        )
    FranchiseResearchArtifact.objects.create(
        research_import=research_import,
        artifact_type=FranchiseResearchArtifact.TYPE_FINALIZATION,
        external_id=str(finalization_id),
        schema_version=artifact["schema_version"],
        prompt_version="",
        reference=str(artifact_path.resolve()),
        sha256=artifact_sha256,
        payload=artifact,
    )
    publication_actions = project_approved_research(finalization)
    FranchiseResearchEvent.objects.create(
        workspace=workspace,
        event_type="workspace_finalized",
        message="Workbench zamrożono i dołączono do importu.",
        metadata={
            "finalization_id": str(finalization_id),
            "research_import_id": research_import.pk,
            "artifact_sha256": artifact_sha256,
            "published_profile_fields": sum(
                item["status"] == "projected" for item in publication_actions
            ),
        },
        actor=actor,
    )
    return finalization, True
