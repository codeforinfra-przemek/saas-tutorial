"""File-backed PL:L1 benchmark workspace and campaign exporter.

The JSON artifacts remain portable and CLI-compatible.  Django only provides
safe editing, progress reporting and a deterministic export from campaign
results; it does not introduce a second benchmark data model in the database.
"""

from __future__ import annotations

import fcntl
import json
import re
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings


REPOSITORY_ROOT = settings.BASE_DIR.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from datacollector.benchmark import (  # noqa: E402
    BenchmarkSubmission,
    BenchmarkCampaignExport,
    BenchmarkValidationError,
    GoldSet,
    evaluate_submission,
    gold_set_readiness,
    load_benchmark_spec,
    load_gold_set,
    load_submission,
    save_gold_set,
    save_submission,
    submission_readiness,
)
from datacollector.storage.json_store import (  # noqa: E402
    load_extraction_results,
    load_normalizer_results,
    load_search_results,
)

from .models import (  # noqa: E402
    Franchise,
    FranchiseResearchCampaign,
    FranchiseResearchJob,
    FranchiseResearchLaunch,
    FranchiseResearchReviewField,
)


BENCHMARK_FILENAMES = {
    "gold": "pl-l1-gold-v1.json",
    "manual": "pl-l1-manual-v1.json",
    "pipeline": "pl-l1-pipeline-v1.json",
    "experiment": "pl-l1-ai-assisted-experiment-v1.json",
}


class ResearchBenchmarkError(ValueError):
    """Raised when UI input or campaign lineage cannot be exported safely."""


def benchmark_directory() -> Path:
    configured = getattr(settings, "RESEARCH_BENCHMARK_DIR", None)
    return Path(configured or REPOSITORY_ROOT / "datacollector" / "benchmarks").resolve()


def benchmark_paths() -> dict[str, Path]:
    root = benchmark_directory()
    return {key: root / filename for key, filename in BENCHMARK_FILENAMES.items()}


def benchmark_campaign_scope() -> dict:
    """Resolve the exact benchmark cohort without changing catalogue status."""

    spec = load_benchmark_spec()
    by_slug = {
        franchise.slug: franchise
        for franchise in Franchise.objects.filter(
            slug__in=[brand.slug for brand in spec.brands]
        ).select_related("category")
    }
    rows = []
    for definition in spec.brands:
        franchise = by_slug.get(definition.slug)
        active_launch = False
        previously_researched = False
        if franchise is not None:
            active_launch = franchise.research_launches.filter(
                status__in=[
                    FranchiseResearchLaunch.STATUS_QUEUED,
                    FranchiseResearchLaunch.STATUS_RUNNING,
                ]
            ).exists()
            previously_researched = (
                franchise.research_workspaces.exists()
                or franchise.research_imports.exists()
            )
        rows.append(
            {
                "definition": definition,
                "franchise": franchise,
                "exists": franchise is not None,
                "is_active": franchise.is_active if franchise is not None else False,
                "active_launch": active_launch,
                "previously_researched": previously_researched,
            }
        )
    missing = [row["definition"].slug for row in rows if not row["exists"]]
    busy = [row["definition"].slug for row in rows if row["active_launch"]]
    return {
        "rows": rows,
        "franchises": [row["franchise"] for row in rows if row["franchise"]],
        "total": len(rows),
        "available": sum(row["exists"] for row in rows),
        "inactive": sum(row["exists"] and not row["is_active"] for row in rows),
        "previously_researched": sum(row["previously_researched"] for row in rows),
        "missing_slugs": missing,
        "busy_slugs": busy,
        "ready": not missing and not busy,
    }


@contextmanager
def _artifact_lock():
    root = benchmark_directory()
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".workbench.lock").open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _artifact(kind: str) -> GoldSet | BenchmarkSubmission:
    paths = benchmark_paths()
    try:
        path = paths[kind]
    except KeyError as exc:
        raise ResearchBenchmarkError("Nieznany artefakt benchmarku.") from exc
    try:
        return load_gold_set(path) if kind == "gold" else load_submission(path)
    except BenchmarkValidationError as exc:
        raise ResearchBenchmarkError(str(exc)) from exc


def _brand(artifact, slug: str):
    try:
        return next(item for item in artifact.brands if item.slug == slug)
    except StopIteration as exc:
        raise ResearchBenchmarkError(f"Marka {slug!r} nie należy do benchmarku.") from exc


def update_gold_field(slug: str, target_field: str, values: dict) -> None:
    with _artifact_lock():
        artifact = _artifact("gold")
        brand = _brand(artifact, slug)
        try:
            field = next(item for item in brand.fields if item.target_field == target_field)
        except StopIteration as exc:
            raise ResearchBenchmarkError("Pole nie należy do benchmarku.") from exc
        for key in (
            "status",
            "canonical_value",
            "source_url",
            "source_type",
            "observed_at",
            "valid_as_of",
            "notes",
        ):
            setattr(field, key, values.get(key) or (None if key.endswith("_at") or key == "valid_as_of" else ""))
        try:
            validated = GoldSet.model_validate(artifact.model_dump())
            save_gold_set(benchmark_paths()["gold"], validated, overwrite=True)
        except (ValueError, BenchmarkValidationError) as exc:
            raise ResearchBenchmarkError(str(exc)) from exc


def update_submission_field(
    kind: str,
    slug: str,
    target_field: str,
    values: dict,
) -> None:
    if kind not in {"manual", "pipeline"}:
        raise ResearchBenchmarkError("To nie jest artefakt submission.")
    with _artifact_lock():
        artifact = _artifact(kind)
        brand = _brand(artifact, slug)
        try:
            field = next(item for item in brand.fields if item.target_field == target_field)
        except StopIteration as exc:
            raise ResearchBenchmarkError("Pole nie należy do benchmarku.") from exc
        for key in (
            "proposal_status",
            "proposed_value",
            "review_decision",
            "source_url",
            "source_type",
            "observed_at",
            "valid_as_of",
            "is_demo_value",
            "demo_disclosed",
            "notes",
        ):
            value = values.get(key)
            if key in {"observed_at", "valid_as_of"} and not value:
                value = None
            elif key in {"is_demo_value", "demo_disclosed"}:
                value = bool(value)
            elif value is None:
                value = ""
            setattr(field, key, value)
        try:
            validated = BenchmarkSubmission.model_validate(artifact.model_dump())
            save_submission(benchmark_paths()[kind], validated, overwrite=True)
        except (ValueError, BenchmarkValidationError) as exc:
            raise ResearchBenchmarkError(str(exc)) from exc


def update_submission_metrics(kind: str, slug: str, values: dict) -> None:
    if kind not in {"manual", "pipeline"}:
        raise ResearchBenchmarkError("To nie jest artefakt submission.")
    with _artifact_lock():
        artifact = _artifact(kind)
        brand = _brand(artifact, slug)
        for key in (
            "tasks_attempted",
            "tasks_total",
            "research_minutes",
            "review_minutes",
            "known_cost_usd",
        ):
            setattr(brand, key, values[key])
        try:
            validated = BenchmarkSubmission.model_validate(artifact.model_dump())
            save_submission(benchmark_paths()[kind], validated, overwrite=True)
        except (ValueError, BenchmarkValidationError) as exc:
            raise ResearchBenchmarkError(str(exc)) from exc


def _source_metadata(
    launch: FranchiseResearchLaunch,
    workspace=None,
) -> dict[str, dict]:
    source_reference = launch.sources_reference
    if workspace and workspace.normalized_reference:
        try:
            normalized, _ = load_normalizer_results(
                _artifact_path(workspace.normalized_reference)
            )
            extraction, _ = load_extraction_results(
                _artifact_path(normalized.extraction_reference)
            )
            source_reference = extraction.search_reference
        except Exception:
            source_reference = launch.sources_reference
    if not source_reference:
        return {}
    source_path = Path(source_reference)
    if not source_path.is_absolute():
        source_path = REPOSITORY_ROOT / source_path
    try:
        search, _search_sha256 = load_search_results(source_path.resolve())
    except Exception:
        return {}
    return {
        source.source_id: {
            "url": source.canonical_url,
            "source_type": source.source_type.value,
            "observed_at": source.discovered_at.date(),
        }
        for source in search.sources
    }


def _artifact_path(reference: str) -> Path:
    path = Path(reference)
    return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()


def _valid_as_of_metadata(
    launch: FranchiseResearchLaunch,
    workspace=None,
) -> dict[str, date]:
    """Recover explicit effective dates without asking another model."""

    normalized_reference = (
        workspace.normalized_reference
        if workspace and workspace.normalized_reference
        else launch.normalized_reference
    )
    extraction_reference = launch.extractions_reference
    if normalized_reference:
        try:
            normalized, _ = load_normalizer_results(
                _artifact_path(normalized_reference)
            )
            extraction_reference = normalized.extraction_reference
        except Exception:
            pass
    if not normalized_reference or not extraction_reference:
        return {}
    try:
        normalized, _normalized_sha256 = load_normalizer_results(
            _artifact_path(normalized_reference)
        )
        extraction, _extraction_sha256 = load_extraction_results(
            _artifact_path(extraction_reference)
        )
    except Exception:
        return {}
    claims = {claim.claim_id: claim for claim in extraction.claims}
    result = {}
    for value in normalized.normalized_values:
        candidates = []
        for claim_id in value.claim_ids:
            claim = claims.get(claim_id)
            if claim is None:
                continue
            for raw in (claim.effective_date_text, claim.as_of_text):
                match = re.search(r"\b(20\d{2}-[01]\d-[0-3]\d)\b", raw or "")
                if match:
                    try:
                        candidates.append(date.fromisoformat(match.group(1)))
                    except ValueError:
                        pass
        if candidates:
            result[value.normalized_value_id] = max(candidates)
    return result


def _first_source(field, sources: dict[str, dict]) -> dict:
    for source_id in field.source_ids:
        if source_id in sources:
            return sources[source_id]
    for evidence in field.evidence:
        if evidence.get("url"):
            return {
                "url": evidence["url"],
                "source_type": "unknown",
                "observed_at": field.updated_at.date(),
            }
    return {}


def _duration_minutes(started, completed) -> float:
    if not started or not completed or completed < started:
        return 0
    return round((completed - started).total_seconds() / 60, 2)


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _effective_workspace(launch: FranchiseResearchLaunch):
    """Use the deepest descendant of the exact campaign plan lineage."""

    initial = launch.result_workspace
    if initial is None:
        return None
    return (
        initial.__class__.objects.filter(
            franchise=launch.franchise,
            plan_run_id=initial.plan_run_id,
        )
        .order_by("-evaluated_tasks", "-created_at")
        .first()
        or initial
    )


def _continuation_metrics(workspace) -> tuple[Decimal, float]:
    if workspace is None:
        return Decimal("0"), 0
    jobs = FranchiseResearchJob.objects.filter(
        workspace__franchise=workspace.franchise,
        workspace__plan_run_id=workspace.plan_run_id,
    )
    cost = sum(
        (_decimal(job.cost_summary.get("estimated_cost_usd")) for job in jobs),
        start=Decimal("0"),
    )
    minutes = sum(
        (
            _duration_minutes(job.started_at, job.completed_at)
            for job in jobs
            if job.started_at and job.completed_at
        ),
        start=0,
    )
    return cost, round(minutes, 2)


def export_campaign_submission(
    campaign: FranchiseResearchCampaign,
    *,
    exported_by: str = "",
) -> dict:
    """Materialize a partial or complete pipeline submission from one campaign."""

    spec = load_benchmark_spec()
    if campaign.profile_id not in {"PL:L1", spec.profile_id}:
        raise ResearchBenchmarkError(
            f"Kampania ma profil {campaign.profile_id}; benchmark wymaga PL:L1 ({spec.profile_id})."
        )
    launches = {
        launch.franchise.slug: launch
        for launch in campaign.launches.select_related(
            "franchise", "result_workspace"
        ).prefetch_related("result_workspace__review_fields")
    }
    benchmark_slugs = {brand.slug for brand in spec.brands}
    unexpected = set(launches) - benchmark_slugs
    if unexpected:
        raise ResearchBenchmarkError(
            "Kampania zawiera marki spoza benchmarku: " + ", ".join(sorted(unexpected))
        )

    with _artifact_lock():
        submission = _artifact("pipeline")
        for brand in submission.brands:
            launch = launches.get(brand.slug)
            if launch is None:
                continue
            workspace = _effective_workspace(launch)
            brand.tasks_total = int(
                (workspace.planned_tasks if workspace else 0)
                or launch.result_summary.get("planned_tasks")
                or brand.tasks_total
            )
            brand.tasks_attempted = min(
                brand.tasks_total,
                int(
                    (workspace.evaluated_tasks if workspace else 0)
                    or launch.result_summary.get("evaluated_tasks")
                    or 0
                ),
            )
            continuation_cost, continuation_minutes = _continuation_metrics(workspace)
            brand.research_minutes = round(
                _duration_minutes(launch.started_at, launch.completed_at)
                + continuation_minutes,
                2,
            )
            brand.research_measurement = "imported"
            if workspace and workspace.reviewed_at:
                brand.review_minutes = _duration_minutes(
                    workspace.created_at, workspace.reviewed_at
                )
                brand.review_measurement = "imported"
            brand.known_cost_usd = _decimal(
                launch.cost_summary.get("estimated_cost_usd")
            ) + continuation_cost
            if not workspace:
                continue
            sources = _source_metadata(launch, workspace)
            valid_as_of = _valid_as_of_metadata(launch, workspace)
            by_target = {}
            for row in workspace.review_fields.all():
                current = by_target.get(row.target_field)
                if current is None or (row.proposed_values and not current.proposed_values):
                    by_target[row.target_field] = row
            for field in brand.fields:
                field.proposal_status = "not_assessed"
                field.proposed_value = ""
                field.review_decision = "not_reviewed"
                field.source_url = ""
                field.source_type = ""
                field.observed_at = None
                field.valid_as_of = None
                field.is_demo_value = False
                field.demo_disclosed = False
                field.notes = ""
                review = by_target.get(field.target_field)
                if review is None:
                    continue
                proposed = bool(review.effective_value)
                documented_gap = (
                    review.decision
                    == FranchiseResearchReviewField.DECISION_DOCUMENTED_GAP
                )
                field.proposal_status = (
                    "proposed" if proposed else "gap" if documented_gap else "not_assessed"
                )
                field.proposed_value = review.effective_value if proposed else ""
                field.review_decision = {
                    FranchiseResearchReviewField.DECISION_ACCEPTED: "accepted_unchanged",
                    FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED: "accepted_edited",
                    FranchiseResearchReviewField.DECISION_REJECTED: "rejected",
                    FranchiseResearchReviewField.DECISION_DOCUMENTED_GAP: "gap",
                }.get(review.decision, "not_reviewed")
                source = _first_source(review, sources)
                field.source_url = source.get("url", "")
                field.source_type = source.get("source_type", "")
                field.observed_at = source.get("observed_at")
                for proposed_value in review.proposed_values:
                    value_id = proposed_value.get("id")
                    if value_id in valid_as_of:
                        field.valid_as_of = valid_as_of[value_id]
                        break
                field.notes = review.reviewer_note or ""
                field.is_demo_value = False
                field.demo_disclosed = False
        submission.export_history.append(
            BenchmarkCampaignExport(
                campaign_id=str(campaign.campaign_id),
                campaign_name=campaign.name,
                exported_at=datetime.now(timezone.utc),
                exported_by=exported_by,
                brand_slugs=sorted(set(launches) & benchmark_slugs),
            )
        )
        submission.export_history = submission.export_history[-100:]
        try:
            validated = BenchmarkSubmission.model_validate(submission.model_dump())
            save_submission(benchmark_paths()["pipeline"], validated, overwrite=True)
        except (ValueError, BenchmarkValidationError) as exc:
            raise ResearchBenchmarkError(str(exc)) from exc
    readiness = submission_readiness(spec, validated)
    return {
        "campaign_id": str(campaign.campaign_id),
        "campaign": campaign.name,
        "matched_brands": len(set(launches) & benchmark_slugs),
        "benchmark_brands": len(benchmark_slugs),
        "ready_brands": readiness["brands_ready"],
        "path": str(benchmark_paths()["pipeline"]),
    }


def benchmark_dashboard() -> dict:
    spec = load_benchmark_spec()
    gold = _artifact("gold")
    manual = _artifact("manual")
    pipeline = _artifact("pipeline")
    gold_progress = gold_set_readiness(spec, gold)
    manual_progress = submission_readiness(spec, manual)
    pipeline_progress = submission_readiness(spec, pipeline)
    gold_rows = {row["slug"]: row for row in gold_progress["brands"]}
    manual_rows = {row["slug"]: row for row in manual_progress["brands"]}
    pipeline_rows = {row["slug"]: row for row in pipeline_progress["brands"]}
    brands = []
    for definition in spec.brands:
        brands.append(
            {
                "definition": definition,
                "gold": gold_rows[definition.slug],
                "manual": manual_rows[definition.slug],
                "pipeline": pipeline_rows[definition.slug],
            }
        )
    experiment = None
    try:
        experiment = json.loads(
            benchmark_paths()["experiment"].read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "spec": spec,
        "gold": gold,
        "manual": manual,
        "pipeline": pipeline,
        "progress": {
            "gold": gold_progress,
            "manual": manual_progress,
            "pipeline": pipeline_progress,
        },
        "brands": brands,
        "paths": benchmark_paths(),
        "manual_evaluation": evaluate_submission(spec, manual, gold_set=gold),
        "pipeline_evaluation": evaluate_submission(spec, pipeline, gold_set=gold),
        "experiment": experiment,
    }


def benchmark_gold_dashboard() -> dict:
    """Return only the blind reference artifact, never either submission."""

    spec = load_benchmark_spec()
    gold = _artifact("gold")
    progress = gold_set_readiness(spec, gold)
    progress_by_slug = {row["slug"]: row for row in progress["brands"]}
    return {
        "spec": spec,
        "gold": gold,
        "progress": progress,
        "brands": [
            {
                "definition": definition,
                "gold": progress_by_slug[definition.slug],
            }
            for definition in spec.brands
        ],
        "path": benchmark_paths()["gold"],
    }


def benchmark_gold_brand(slug: str) -> dict:
    dashboard = benchmark_gold_dashboard()
    spec = dashboard["spec"]
    definition = next((item for item in spec.brands if item.slug == slug), None)
    if definition is None:
        raise ResearchBenchmarkError("Marka nie należy do benchmarku.")
    brand = _brand(dashboard["gold"], slug)
    policies = {field.target_field: field for field in spec.fields}
    return {
        **dashboard,
        "definition": definition,
        "brand_gold": brand,
        "field_rows": [
            {
                "policy": policies[field.target_field],
                "gold": field,
            }
            for field in brand.fields
        ],
    }


def benchmark_brand(slug: str) -> dict:
    dashboard = benchmark_dashboard()
    spec = dashboard["spec"]
    definition = next((item for item in spec.brands if item.slug == slug), None)
    if definition is None:
        raise ResearchBenchmarkError("Marka nie należy do benchmarku.")
    policies = {field.target_field: field for field in spec.fields}
    gold = _brand(dashboard["gold"], slug)
    manual = _brand(dashboard["manual"], slug)
    pipeline = _brand(dashboard["pipeline"], slug)
    rows = []
    for gold_field, manual_field, pipeline_field in zip(
        gold.fields, manual.fields, pipeline.fields, strict=True
    ):
        rows.append(
            {
                "policy": policies[gold_field.target_field],
                "gold": gold_field,
                "manual": manual_field,
                "pipeline": pipeline_field,
            }
        )
    return {
        **dashboard,
        "definition": definition,
        "brand_gold": gold,
        "brand_manual": manual,
        "brand_pipeline": pipeline,
        "field_rows": rows,
    }


def eligible_campaigns():
    spec = load_benchmark_spec()
    benchmark_slugs = {brand.slug for brand in spec.brands}
    campaigns = FranchiseResearchCampaign.objects.filter(
        profile_id__in=["PL:L1", spec.profile_id]
    ).prefetch_related("launches__franchise").order_by("-queued_at")
    return [
        campaign
        for campaign in campaigns
        if (slugs := {launch.franchise.slug for launch in campaign.launches.all()})
        and slugs.issubset(benchmark_slugs)
    ]
