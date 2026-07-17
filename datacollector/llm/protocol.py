"""Provider-neutral interface used by the Planner."""

from __future__ import annotations

from typing import Protocol

from ..schemas import CatalogQuestion, PlannerDraft, PlannerInput


class PlannerLLM(Protocol):
    @property
    def model_name(self) -> str: ...

    def generate(
        self,
        planner_input: PlannerInput,
        questions: list[CatalogQuestion],
        system_prompt: str,
    ) -> PlannerDraft: ...
