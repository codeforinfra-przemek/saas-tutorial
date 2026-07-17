"""Provider-neutral interface used by the Planner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..schemas import AgentIterationUsage, CatalogQuestion, PlannerDraft, PlannerInput


@dataclass(frozen=True)
class PlannerGeneration:
    draft: PlannerDraft
    usage: AgentIterationUsage


class PlannerLLM(Protocol):
    @property
    def model_name(self) -> str: ...

    def generate(
        self,
        planner_input: PlannerInput,
        questions: list[CatalogQuestion],
        system_prompt: str,
        *,
        iteration: int,
    ) -> PlannerGeneration: ...
