"""Provider-neutral interface used by the Planner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..schemas import (
    AgentIterationUsage,
    CatalogQuestion,
    PlannerDraft,
    PlannerInput,
    ResearchPlan,
    ResearchTask,
    SearchAction,
    SearcherDraft,
)


@dataclass(frozen=True)
class PlannerGeneration:
    draft: PlannerDraft
    usage: AgentIterationUsage


@dataclass(frozen=True)
class ProviderSearchSource:
    url: str
    title: str = ""


@dataclass(frozen=True)
class SearcherGeneration:
    draft: SearcherDraft
    usage: AgentIterationUsage
    actions: list[SearchAction]
    provider_sources: list[ProviderSearchSource]


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


class SearcherLLM(Protocol):
    @property
    def model_name(self) -> str: ...

    def generate(
        self,
        plan: ResearchPlan,
        tasks: list[ResearchTask],
        system_prompt: str,
        *,
        iteration: int,
        max_search_calls: int,
    ) -> SearcherGeneration: ...
