"""Manual gold-set scaffolding and empirical PL:L1 benchmark evaluation.

The benchmark is deliberately separate from production research artifacts.  A
gold set must be prepared independently by a human researcher; otherwise the
pipeline would be grading its own output.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_BENCHMARK_SPEC_PATH = (
    Path(__file__).resolve().parent / "catalogs" / "l1_benchmark_v1.yaml"
)


class BenchmarkValidationError(ValueError):
    """Raised for an invalid benchmark specification or result artifact."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PilotGates(StrictModel):
    all_tasks_attempted: bool = True
    proposed_fields_min: int = Field(ge=1)
    proposed_fields_target: int = Field(ge=1)
    accepted_unchanged_rate_min: float = Field(ge=0, le=1)
    review_minutes_max: float = Field(gt=0)
    accepted_fields_per_usd_min: float = Field(gt=0)
    unmarked_demo_values_max: int = Field(ge=0)
    public_numeric_metadata_rate_min: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_targets(self) -> "PilotGates":
        if self.proposed_fields_target < self.proposed_fields_min:
            raise ValueError("The proposal target cannot be below its minimum.")
        return self


class BenchmarkBrand(StrictModel):
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    source_availability: Literal["high", "medium", "low"]
    rationale: str = Field(min_length=10, max_length=1000)


class BenchmarkFieldPolicy(StrictModel):
    target_field: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    label: str = Field(min_length=1, max_length=200)
    value_type: str = Field(min_length=1, max_length=100)
    priority: Literal["critical", "high", "medium", "low"]
    freshness_mode: Literal[
        "stable", "active_source", "max_age", "explicit_as_of", "follows_value"
    ]
    max_age_days: int | None = Field(default=None, ge=1)
    accepted_source_types: list[str] = Field(min_length=1)
    minimum_sources: int = Field(ge=1, le=10)
    numeric: bool

    @model_validator(mode="after")
    def validate_freshness(self) -> "BenchmarkFieldPolicy":
        if self.freshness_mode in {"max_age", "explicit_as_of", "follows_value"}:
            if self.max_age_days is None:
                raise ValueError(
                    f"{self.target_field} requires max_age_days for its freshness mode."
                )
        elif self.max_age_days is not None:
            raise ValueError(
                f"{self.target_field} must not set max_age_days for stable/active_source."
            )
        return self


class BenchmarkSpec(StrictModel):
    version: str = Field(min_length=1)
    profile_id: str = Field(pattern=r"^[A-Z]{2}:L[1-3]:v[1-9][0-9]*$")
    title: str = Field(min_length=3, max_length=300)
    pilot_gates: PilotGates
    brands: list[BenchmarkBrand] = Field(min_length=1)
    fields: list[BenchmarkFieldPolicy] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_contract(self) -> "BenchmarkSpec":
        slugs = [brand.slug for brand in self.brands]
        targets = [field.target_field for field in self.fields]
        if len(slugs) != len(set(slugs)):
            raise ValueError("Benchmark brand slugs must be unique.")
        if len(targets) != len(set(targets)):
            raise ValueError("Benchmark target fields must be unique.")
        return self


GoldStatus = Literal["pending", "found", "not_public", "not_applicable"]


class GoldField(StrictModel):
    target_field: str
    status: GoldStatus = "pending"
    canonical_value: str = ""
    source_url: str = ""
    source_type: str = ""
    observed_at: date | None = None
    valid_as_of: date | None = None
    notes: str = ""

    @model_validator(mode="after")
    def validate_found(self) -> "GoldField":
        if self.status == "found" and not all(
            (self.canonical_value, self.source_url, self.source_type, self.observed_at)
        ):
            raise ValueError(
                f"Gold field {self.target_field} marked found requires value, source, "
                "source type and observed_at."
            )
        return self


class GoldBrand(StrictModel):
    slug: str
    fields: list[GoldField]


class GoldSet(StrictModel):
    artifact_type: Literal["pl_l1_gold_set"] = "pl_l1_gold_set"
    spec_version: str
    profile_id: str
    researcher: str = ""
    methodology: Literal[
        "unspecified", "human_independent", "ai_independent_proxy"
    ] = "unspecified"
    independence_statement: str = ""
    provider_model: str = ""
    created_at: datetime
    instructions: list[str]
    brands: list[GoldBrand]


ProposalStatus = Literal["not_assessed", "proposed", "gap"]
ReviewDecision = Literal[
    "not_reviewed", "accepted_unchanged", "accepted_edited", "rejected", "gap"
]


class SubmissionField(StrictModel):
    target_field: str
    proposal_status: ProposalStatus = "not_assessed"
    proposed_value: str = ""
    review_decision: ReviewDecision = "not_reviewed"
    source_url: str = ""
    source_type: str = ""
    observed_at: date | None = None
    valid_as_of: date | None = None
    is_demo_value: bool = False
    demo_disclosed: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def validate_proposal(self) -> "SubmissionField":
        if self.proposal_status == "proposed" and not self.proposed_value:
            raise ValueError(
                f"Proposed field {self.target_field} requires proposed_value."
            )
        if self.proposal_status != "proposed" and self.review_decision in {
            "accepted_unchanged",
            "accepted_edited",
            "rejected",
        }:
            raise ValueError(
                f"Field {self.target_field} cannot have a review decision without a proposal."
            )
        return self


class SubmissionBrand(StrictModel):
    slug: str
    tasks_attempted: int = Field(default=0, ge=0)
    tasks_total: int = Field(default=7, ge=1)
    research_minutes: float = Field(default=0, ge=0)
    review_minutes: float = Field(default=0, ge=0)
    research_measurement: Literal[
        "not_recorded", "human_active", "ai_assisted_wall_clock", "imported"
    ] = "not_recorded"
    review_measurement: Literal[
        "not_recorded", "human_active", "ai_assisted_wall_clock", "imported"
    ] = "not_recorded"
    known_cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    fields: list[SubmissionField]

    @model_validator(mode="after")
    def validate_task_counts(self) -> "SubmissionBrand":
        if self.tasks_attempted > self.tasks_total:
            raise ValueError("tasks_attempted cannot exceed tasks_total.")
        return self


class BenchmarkCampaignExport(StrictModel):
    campaign_id: str = Field(min_length=36, max_length=36)
    campaign_name: str = Field(min_length=1, max_length=200)
    exported_at: datetime
    exported_by: str = Field(default="", max_length=150)
    brand_slugs: list[str] = Field(default_factory=list, max_length=100)


class BenchmarkSubmission(StrictModel):
    artifact_type: Literal["pl_l1_benchmark_submission"] = (
        "pl_l1_benchmark_submission"
    )
    spec_version: str
    profile_id: str
    method: Literal["researcher_chatgpt", "pipeline"]
    operator: str = ""
    methodology_notes: list[str] = Field(default_factory=list, max_length=20)
    created_at: datetime
    brands: list[SubmissionBrand]
    export_history: list[BenchmarkCampaignExport] = Field(default_factory=list, max_length=100)


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkValidationError(f"Cannot read benchmark artifact {path}: {exc}") from exc


def _write_json(
    path: Path,
    value: BaseModel | dict,
    *,
    overwrite: bool = False,
) -> None:
    if path.exists() and not overwrite:
        raise BenchmarkValidationError(
            f"Benchmark artifact already exists: {path}. Use an explicit overwrite action."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_benchmark_spec(path: Path | str = DEFAULT_BENCHMARK_SPEC_PATH) -> BenchmarkSpec:
    source = Path(path)
    try:
        payload = yaml.safe_load(source.read_text(encoding="utf-8"))
        return BenchmarkSpec.model_validate(payload)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise BenchmarkValidationError(f"Invalid L1 benchmark specification: {exc}") from exc


def field_policy_map(profile_id: str | None) -> dict[str, BenchmarkFieldPolicy]:
    """Return field-level policy only for the exact calibrated profile release."""

    spec = load_benchmark_spec()
    if profile_id != spec.profile_id:
        return {}
    return {field.target_field: field for field in spec.fields}


def create_gold_set(spec: BenchmarkSpec, *, researcher: str = "") -> GoldSet:
    return GoldSet(
        spec_version=spec.version,
        profile_id=spec.profile_id,
        researcher=researcher,
        methodology="unspecified",
        independence_statement=(
            "Gold Set must be produced without access to either benchmark submission."
        ),
        created_at=datetime.now(timezone.utc),
        instructions=[
            "Fill this artifact independently of pipeline results.",
            "Use found only with a value, direct source URL, source type and observation date.",
            "Use not_public for a field a competent researcher could not find publicly; do not guess.",
            "Record the source's effective/as-of date separately from the observation date where available.",
        ],
        brands=[
            GoldBrand(
                slug=brand.slug,
                fields=[GoldField(target_field=field.target_field) for field in spec.fields],
            )
            for brand in spec.brands
        ],
    )


def create_submission(
    spec: BenchmarkSpec,
    *,
    method: Literal["researcher_chatgpt", "pipeline"],
    operator: str = "",
) -> BenchmarkSubmission:
    return BenchmarkSubmission(
        spec_version=spec.version,
        profile_id=spec.profile_id,
        method=method,
        operator=operator,
        methodology_notes=[],
        created_at=datetime.now(timezone.utc),
        brands=[
            SubmissionBrand(
                slug=brand.slug,
                fields=[
                    SubmissionField(target_field=field.target_field)
                    for field in spec.fields
                ],
            )
            for brand in spec.brands
        ],
    )


def save_gold_set(path: Path, artifact: GoldSet, *, overwrite: bool = False) -> None:
    _write_json(path, artifact, overwrite=overwrite)


def save_submission(
    path: Path,
    artifact: BenchmarkSubmission,
    *,
    overwrite: bool = False,
) -> None:
    _write_json(path, artifact, overwrite=overwrite)


def load_gold_set(path: Path) -> GoldSet:
    try:
        return GoldSet.model_validate(_load_json(path))
    except ValueError as exc:
        raise BenchmarkValidationError(f"Invalid gold set: {exc}") from exc


def load_submission(path: Path) -> BenchmarkSubmission:
    try:
        return BenchmarkSubmission.model_validate(_load_json(path))
    except ValueError as exc:
        raise BenchmarkValidationError(f"Invalid benchmark submission: {exc}") from exc


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _canonical(value: str) -> str:
    return " ".join(value.casefold().split())


def submission_readiness(
    spec: BenchmarkSpec,
    submission: BenchmarkSubmission,
) -> dict:
    """Explain whether a submission contains enough observations to score it.

    This is intentionally separate from the quality gates. A blank template is
    not a failed research method; it is simply not ready to compare.
    """

    expected_fields = {field.target_field for field in spec.fields}
    rows = []
    for brand in submission.brands:
        fields = {field.target_field: field for field in brand.fields}
        issues = []
        if brand.tasks_attempted != brand.tasks_total:
            issues.append("tasks_not_fully_attempted")
        if expected_fields - fields.keys():
            issues.append("fields_missing_from_artifact")
        if any(field.proposal_status == "not_assessed" for field in fields.values()):
            issues.append("fields_not_assessed")
        if any(
            field.proposal_status == "proposed"
            and field.review_decision == "not_reviewed"
            for field in fields.values()
        ):
            issues.append("proposals_not_reviewed")
        if any(
            field.proposal_status == "gap" and field.review_decision != "gap"
            for field in fields.values()
        ):
            issues.append("gaps_not_reviewed")
        if brand.review_minutes <= 0:
            issues.append("review_time_not_recorded")
        if brand.known_cost_usd <= 0:
            issues.append("known_cost_not_recorded")
        rows.append(
            {
                "slug": brand.slug,
                "ready": not issues,
                "issues": issues,
                "assessed_fields": sum(
                    field.proposal_status != "not_assessed" for field in fields.values()
                ),
                "total_fields": len(expected_fields),
            }
        )
    return {
        "ready": bool(rows) and all(row["ready"] for row in rows),
        "brands_ready": sum(row["ready"] for row in rows),
        "brands_total": len(rows),
        "brands": rows,
    }


def gold_set_readiness(spec: BenchmarkSpec, gold_set: GoldSet) -> dict:
    rows = []
    for brand in gold_set.brands:
        pending = sum(field.status == "pending" for field in brand.fields)
        rows.append(
            {
                "slug": brand.slug,
                "ready": pending == 0,
                "completed_fields": len(spec.fields) - pending,
                "total_fields": len(spec.fields),
            }
        )
    return {
        "ready": bool(rows) and all(row["ready"] for row in rows),
        "brands_ready": sum(row["ready"] for row in rows),
        "brands_total": len(rows),
        "brands": rows,
    }


def evaluate_submission(
    spec: BenchmarkSpec,
    submission: BenchmarkSubmission,
    *,
    gold_set: GoldSet | None = None,
) -> dict:
    if (submission.spec_version, submission.profile_id) != (
        spec.version,
        spec.profile_id,
    ):
        raise BenchmarkValidationError("Submission does not match benchmark specification.")
    expected_slugs = [brand.slug for brand in spec.brands]
    if [brand.slug for brand in submission.brands] != expected_slugs:
        raise BenchmarkValidationError("Submission brand order/scope differs from the specification.")
    expected_fields = [field.target_field for field in spec.fields]
    policy_by_field = {field.target_field: field for field in spec.fields}
    gold_by_brand: dict[str, dict[str, GoldField]] = {}
    if gold_set is not None:
        if (gold_set.spec_version, gold_set.profile_id) != (
            spec.version,
            spec.profile_id,
        ):
            raise BenchmarkValidationError("Gold set does not match benchmark specification.")
        gold_by_brand = {
            brand.slug: {field.target_field: field for field in brand.fields}
            for brand in gold_set.brands
        }

    readiness = submission_readiness(spec, submission)
    brand_readiness = {item["slug"]: item for item in readiness["brands"]}
    brand_results = []
    totals = {
        "proposed": 0,
        "reviewed": 0,
        "accepted": 0,
        "accepted_unchanged": 0,
        "review_minutes": 0.0,
        "known_cost_usd": Decimal("0"),
        "numeric_proposals": 0,
        "numeric_with_metadata": 0,
        "unmarked_demo_values": 0,
        "gold_comparable": 0,
        "gold_exact": 0,
    }
    review_measurement_methods: set[str] = set()
    research_measurement_methods: set[str] = set()
    for brand in submission.brands:
        if [field.target_field for field in brand.fields] != expected_fields:
            raise BenchmarkValidationError(
                f"Submission fields for {brand.slug} differ from the specification."
            )
        proposed = [field for field in brand.fields if field.proposal_status == "proposed"]
        reviewed = [field for field in proposed if field.review_decision != "not_reviewed"]
        accepted = [
            field
            for field in proposed
            if field.review_decision in {"accepted_unchanged", "accepted_edited"}
        ]
        unchanged = [field for field in proposed if field.review_decision == "accepted_unchanged"]
        numeric = [field for field in proposed if policy_by_field[field.target_field].numeric]
        numeric_with_metadata = [
            field
            for field in numeric
            if field.source_type in policy_by_field[field.target_field].accepted_source_types
            and field.source_url
            and field.observed_at
        ]
        unmarked_demo = [
            field for field in proposed if field.is_demo_value and not field.demo_disclosed
        ]
        comparable = 0
        exact = 0
        for field in proposed:
            gold = gold_by_brand.get(brand.slug, {}).get(field.target_field)
            if gold and gold.status == "found":
                comparable += 1
                exact += _canonical(field.proposed_value) == _canonical(gold.canonical_value)
        efficiency = (
            round(len(accepted) / float(brand.known_cost_usd), 4)
            if brand.known_cost_usd > 0
            else None
        )
        numeric_metadata_rate = (
            _ratio(len(numeric_with_metadata), len(numeric)) if numeric else 1.0
        )
        metrics = {
            "all_tasks_attempted": brand.tasks_attempted == brand.tasks_total,
            "proposed_fields": len(proposed),
            "accepted_fields": len(accepted),
            # The pilot gate is a share of every proposal, not only the subset
            # someone happened to review. Unreviewed proposals therefore cannot
            # inflate the acceptance rate.
            "accepted_unchanged_rate": _ratio(len(unchanged), len(proposed)),
            "review_minutes": brand.review_minutes,
            "accepted_fields_per_usd": efficiency,
            "unmarked_demo_values": len(unmarked_demo),
            # No numeric proposal cannot violate the rule that every public
            # numeric proposal must carry source/date metadata.
            "public_numeric_metadata_rate": numeric_metadata_rate,
            "gold_exact_rate": _ratio(exact, comparable) if comparable else None,
            "gold_comparable_fields": comparable,
        }
        gates = {
            "all_tasks_attempted": metrics["all_tasks_attempted"],
            "proposed_fields_min": len(proposed) >= spec.pilot_gates.proposed_fields_min,
            "accepted_unchanged_rate": metrics["accepted_unchanged_rate"]
            >= spec.pilot_gates.accepted_unchanged_rate_min,
            "review_minutes": 0 < brand.review_minutes
            <= spec.pilot_gates.review_minutes_max,
            "accepted_fields_per_usd": efficiency is not None
            and efficiency >= spec.pilot_gates.accepted_fields_per_usd_min,
            "unmarked_demo_values": len(unmarked_demo)
            <= spec.pilot_gates.unmarked_demo_values_max,
            "public_numeric_metadata_rate": metrics["public_numeric_metadata_rate"]
            >= spec.pilot_gates.public_numeric_metadata_rate_min,
        }
        ready = brand_readiness[brand.slug]["ready"]
        brand_results.append(
            {
                "slug": brand.slug,
                "ready": ready,
                "readiness_issues": brand_readiness[brand.slug]["issues"],
                "metrics": metrics,
                "gates": gates,
                "passed": ready and all(gates.values()),
            }
        )
        totals["proposed"] += len(proposed)
        totals["reviewed"] += len(reviewed)
        totals["accepted"] += len(accepted)
        totals["accepted_unchanged"] += len(unchanged)
        totals["review_minutes"] += brand.review_minutes
        totals["known_cost_usd"] += brand.known_cost_usd
        totals["numeric_proposals"] += len(numeric)
        totals["numeric_with_metadata"] += len(numeric_with_metadata)
        totals["unmarked_demo_values"] += len(unmarked_demo)
        totals["gold_comparable"] += comparable
        totals["gold_exact"] += exact
        review_measurement_methods.add(brand.review_measurement)
        research_measurement_methods.add(brand.research_measurement)

    aggregate_efficiency = (
        round(totals["accepted"] / float(totals["known_cost_usd"]), 4)
        if totals["known_cost_usd"] > 0
        else None
    )
    issue_counts: dict[str, int] = {}
    for row in readiness["brands"]:
        for issue in row["issues"]:
            issue_counts[issue] = issue_counts.get(issue, 0) + 1
    next_actions = []
    if not readiness["ready"]:
        if issue_counts.get("tasks_not_fully_attempted"):
            next_actions.append(
                "Run or export the exact benchmark campaign so every L1 task is attempted."
            )
        if issue_counts.get("fields_not_assessed"):
            next_actions.append(
                "Populate every benchmark field as proposed or as a documented gap."
            )
        if issue_counts.get("proposals_not_reviewed") or issue_counts.get("gaps_not_reviewed"):
            next_actions.append(
                "Complete Human Review for every proposal and documented gap."
            )
        if issue_counts.get("review_time_not_recorded"):
            next_actions.append("Record active Human Review time for every brand.")
        if issue_counts.get("known_cost_not_recorded"):
            next_actions.append("Record the known API cost for every brand.")
    if gold_set is not None and not gold_set_readiness(spec, gold_set)["ready"]:
        next_actions.append(
            "Complete the independent gold set; do not derive it from pipeline output."
        )
    if not next_actions:
        next_actions.append("Review the measured pilot gates and compare both methods.")

    return {
        "benchmark_version": spec.version,
        "profile_id": spec.profile_id,
        "method": submission.method,
        "evaluation_status": "ready" if readiness["ready"] else "not_ready",
        "readiness_issue_counts": issue_counts,
        "next_actions": next_actions,
        "readiness": readiness,
        "brands": brand_results,
        "aggregate": {
            "brands_passed": sum(result["passed"] for result in brand_results),
            "brands_total": len(brand_results),
            "proposed_fields": totals["proposed"],
            "accepted_fields": totals["accepted"],
            "accepted_unchanged_rate": _ratio(
                totals["accepted_unchanged"], totals["proposed"]
            ),
            "review_minutes": round(totals["review_minutes"], 2),
            "known_cost_usd": str(totals["known_cost_usd"]),
            "accepted_fields_per_usd": aggregate_efficiency,
            "unmarked_demo_values": totals["unmarked_demo_values"],
            "public_numeric_metadata_rate": (
                _ratio(totals["numeric_with_metadata"], totals["numeric_proposals"])
                if totals["numeric_proposals"]
                else 1.0
            ),
            "gold_exact_rate": (
                _ratio(totals["gold_exact"], totals["gold_comparable"])
                if totals["gold_comparable"]
                else None
            ),
            "gold_comparable_fields": totals["gold_comparable"],
            "research_measurement_methods": sorted(research_measurement_methods),
            "review_measurement_methods": sorted(review_measurement_methods),
        },
        "passed": readiness["ready"] and all(result["passed"] for result in brand_results),
        "note": (
            "Pilot gates are empirical targets. Recalibrate them after comparing both methods; "
            "do not turn them into publication claims."
        ),
    }
