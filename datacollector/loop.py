"""Stable blueprint for the future multi-agent research loop."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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


class LoopPolicy(BaseModel):
    """Stopping and safety rules that later orchestration must enforce."""

    model_config = ConfigDict(extra="forbid")

    quality_threshold: int = Field(default=80, ge=0, le=100)
    max_rounds: int = Field(default=3, ge=1, le=10)
    require_no_critical_missing: bool = True
    require_human_review_before_import: bool = True
    publish_automatically: bool = False
