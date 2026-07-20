"""Contracts and safety policy for the paid multi-agent research loop."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


LOOP_RUN_SCHEMA_VERSION = "1.1.0"


class LoopValidationError(ValueError):
    """Raised when orchestration cannot continue without violating safety rules."""


class ClosedLoopModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoopAgent(StrEnum):
    PLANNER = "planner"
    SEARCHER = "searcher"
    EXTRACTOR = "extractor"
    CHECKER = "checker"
    RESOLVER = "resolver"
    EXECUTOR = "executor"
    NORMALIZER = "normalizer"
    HUMAN_REVIEW = "human_review"
    IMPORTER = "importer"


LOOP_SEQUENCE = (
    LoopAgent.PLANNER,
    LoopAgent.SEARCHER,
    LoopAgent.EXTRACTOR,
    LoopAgent.CHECKER,
    LoopAgent.RESOLVER,
    LoopAgent.EXECUTOR,
    LoopAgent.NORMALIZER,
    LoopAgent.HUMAN_REVIEW,
    LoopAgent.IMPORTER,
)


class LoopStopReason(StrEnum):
    CHECKER_PASSED = "checker_passed"
    MAX_ROUNDS = "max_rounds"
    PLAN_REPAIR_LIMIT = "plan_repair_limit"
    NO_PROGRESS = "no_progress"
    BUDGET_EXHAUSTED = "budget_exhausted"
    COST_UNKNOWN = "cost_unknown"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


class LoopNextAction(StrEnum):
    HUMAN_REVIEW = "human_review"
    NORMALIZE = "normalize"
    RESUME_LOOP = "resume_loop"
    INSPECT_GAPS = "inspect_gaps"


class LoopPolicy(ClosedLoopModel):
    """Stopping and safety rules enforced by one orchestrator invocation."""

    quality_threshold: int = Field(default=80, ge=0, le=100)
    max_rounds: int = Field(default=3, ge=1, le=20)
    max_estimated_cost_usd: Decimal = Field(default=Decimal("1.00"), gt=0)
    min_quality_improvement: int = Field(default=1, ge=0, le=100)
    max_stagnant_rounds: int = Field(default=2, ge=1, le=10)
    allow_plan_repair_limit: bool = False
    advance_with_documented_gaps: bool = False
    require_no_critical_missing: bool = True
    require_human_review_before_import: bool = True
    publish_automatically: bool = False

    @model_validator(mode="after")
    def validate_exhausted_scope_policy(self) -> "LoopPolicy":
        if self.allow_plan_repair_limit and self.advance_with_documented_gaps:
            raise ValueError(
                "Loop cannot both continue gap repair and advance with those gaps."
            )
        return self


class LoopStageUsage(ClosedLoopModel):
    """Incremental provider usage attributable to one orchestrated stage."""

    stage: Literal["checker", "resolver", "executor", "normalizer"]
    iteration: int = Field(ge=1)
    artifact_reference: str = Field(min_length=1)
    api_attempts_recorded: int = Field(ge=0)
    api_calls_with_usage: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    tool_cost_usd: Decimal = Field(ge=0)
    estimated_cost_usd: Decimal | None = Field(default=None, ge=0)
    token_usage_unknown: bool = False


class LoopRoundResult(ClosedLoopModel):
    """One Checker decision followed by repair/expansion and another Checker."""

    round_number: int = Field(ge=1)
    checker_action: str = Field(min_length=1)
    starting_check_id: str
    starting_check_reference: str = Field(min_length=1)
    starting_check_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    ending_check_id: str
    ending_check_reference: str = Field(min_length=1)
    ending_check_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resolution_reference: str | None = None
    execution_reference: str | None = None
    quality_before: int = Field(ge=0, le=100)
    quality_after: int = Field(ge=0, le=100)
    quality_delta: int = Field(ge=-100, le=100)
    evaluated_tasks_before: int = Field(ge=0)
    evaluated_tasks_after: int = Field(ge=0)
    evaluated_sources_before: int = Field(ge=0)
    evaluated_sources_after: int = Field(ge=0)
    evaluated_claims_before: int = Field(ge=0)
    evaluated_claims_after: int = Field(ge=0)
    critical_missing_before: int = Field(ge=0)
    critical_missing_after: int = Field(ge=0)
    contradictions_before: int = Field(ge=0)
    contradictions_after: int = Field(ge=0)
    verified_fields_before: int = Field(ge=0)
    verified_fields_after: int = Field(ge=0)
    selected_scope_ready_before: bool
    selected_scope_ready_after: bool
    progress_detected: bool
    progress_reasons: list[str]
    regression_reasons: list[str] = Field(default_factory=list)
    stage_usage: list[LoopStageUsage] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_round(self) -> "LoopRoundResult":
        for value, label in (
            (self.starting_check_id, "starting_check_id"),
            (self.ending_check_id, "ending_check_id"),
        ):
            try:
                parsed = UUID(value)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"{label} must be a valid UUIDv4.") from exc
            if parsed.version != 4:
                raise ValueError(f"{label} must be a valid UUIDv4.")
        if self.quality_delta != self.quality_after - self.quality_before:
            raise ValueError("Loop quality delta is inconsistent.")
        if self.progress_detected != bool(self.progress_reasons):
            raise ValueError("Loop progress flag must match progress reasons.")
        return self


class LoopRunResults(ClosedLoopModel):
    """Immutable audit manifest for one bounded paid orchestration session."""

    schema_version: Literal["1.0.0", "1.1.0"] = LOOP_RUN_SCHEMA_VERSION
    loop_id: str
    plan_run_id: str
    plan_reference: str = Field(min_length=1)
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    brand_name: str = Field(min_length=1)
    profile_id: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}:L[1-3]:v[1-9][0-9]*$",
    )
    profile_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    research_level: Literal["L1", "L2", "L3"] | None = None
    started_at: datetime
    completed_at: datetime
    initial_check_id: str
    initial_check_reference: str = Field(min_length=1)
    initial_check_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    final_check_id: str
    final_check_reference: str = Field(min_length=1)
    final_check_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy: LoopPolicy
    rounds: list[LoopRoundResult]
    post_loop_usage: list[LoopStageUsage] = Field(default_factory=list)
    stop_reason: LoopStopReason
    final_quality_score: int = Field(ge=0, le=100)
    final_quality_threshold: int = Field(ge=0, le=100)
    final_scope_complete: bool
    final_checker_passed: bool
    incremental_api_attempts: int = Field(ge=0)
    incremental_input_tokens: int = Field(ge=0)
    incremental_output_tokens: int = Field(ge=0)
    incremental_reasoning_tokens: int = Field(ge=0)
    incremental_total_tokens: int = Field(ge=0)
    incremental_tool_calls: int = Field(ge=0)
    incremental_tool_cost_usd: Decimal = Field(ge=0)
    incremental_estimated_cost_usd: Decimal | None = Field(default=None, ge=0)
    normalization_reference: str | None = None
    recommended_next_action: LoopNextAction
    warnings: list[str]

    @model_validator(mode="after")
    def validate_run(self) -> "LoopRunResults":
        for value, label in (
            (self.loop_id, "loop_id"),
            (self.plan_run_id, "plan_run_id"),
            (self.initial_check_id, "initial_check_id"),
            (self.final_check_id, "final_check_id"),
        ):
            try:
                parsed = UUID(value)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"{label} must be a valid UUIDv4.") from exc
            if parsed.version != 4:
                raise ValueError(f"{label} must be a valid UUIDv4.")
        if self.completed_at < self.started_at:
            raise ValueError("Loop completion cannot precede its start.")
        profile_metadata = (
            self.profile_id,
            self.profile_sha256,
            self.research_level,
        )
        if any(value is not None for value in profile_metadata) and not all(
            value is not None for value in profile_metadata
        ):
            raise ValueError(
                "Loop profile metadata must be either complete or absent."
            )
        if self.schema_version == "1.0.0" and any(
            value is not None for value in profile_metadata
        ):
            raise ValueError(
                "Loop schema 1.0.0 cannot contain research-profile metadata."
            )
        if [item.round_number for item in self.rounds] != list(
            range(1, len(self.rounds) + 1)
        ):
            raise ValueError("Loop round numbers must be consecutive.")
        stages = [
            *(stage for item in self.rounds for stage in item.stage_usage),
            *self.post_loop_usage,
        ]
        if self.incremental_api_attempts != sum(
            stage.api_attempts_recorded for stage in stages
        ):
            raise ValueError("Loop API-attempt total is inconsistent.")
        if self.incremental_total_tokens != sum(
            stage.total_tokens for stage in stages
        ):
            raise ValueError("Loop token total is inconsistent.")
        if self.incremental_tool_calls != sum(stage.tool_calls for stage in stages):
            raise ValueError("Loop tool-call total is inconsistent.")
        known_costs = [stage.estimated_cost_usd for stage in stages]
        expected_cost = (
            sum((cost for cost in known_costs if cost is not None), Decimal("0"))
            if all(cost is not None for cost in known_costs)
            else None
        )
        if self.incremental_estimated_cost_usd != expected_cost:
            raise ValueError("Loop estimated-cost total is inconsistent.")
        if self.normalization_reference is not None and (
            self.recommended_next_action != LoopNextAction.HUMAN_REVIEW
        ):
            raise ValueError("Normalized loop output must route to Human Review.")
        return self
