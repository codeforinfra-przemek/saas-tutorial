"""Provider-neutral interfaces used by the research agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..schemas import (
    AgentIterationUsage,
    CatalogQuestion,
    EvidencePassage,
    ExtractionAttemptFailure,
    ExtractorDraft,
    PlannerDraft,
    PlannerInput,
    ResearchPlan,
    ResearchTask,
    SearchAction,
    SearcherDraft,
    SearchSource,
    SourceDocument,
    ToolUsage,
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


@dataclass(frozen=True)
class ExtractorGeneration:
    draft: ExtractorDraft
    usage: AgentIterationUsage
    source_id: str


class SearcherProviderError(RuntimeError):
    """Raised when a provider response cannot produce a usable search result."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        usage: AgentIterationUsage | None = None,
        usages: list[AgentIterationUsage] | None = None,
        observed_tool_calls: int = 0,
        tool_usage: list[ToolUsage] | None = None,
        agent: str | None = None,
        iteration: int | None = None,
        call_index: int | None = None,
        scope_task_ids: list[str] | None = None,
        requested_model: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        collected_usages = list(usages or [])
        if usage is not None and usage not in collected_usages:
            collected_usages.append(usage)
        self.usages = collected_usages
        self.usage = usage or (collected_usages[-1] if collected_usages else None)
        self.observed_tool_calls = observed_tool_calls
        self.tool_usage = list(
            tool_usage
            if tool_usage is not None
            else self.usage.tool_usage
            if self.usage is not None
            else []
        )
        self.agent = agent or (self.usage.agent if self.usage is not None else None)
        self.iteration = iteration or (
            self.usage.iteration if self.usage is not None else None
        )
        self.call_index = call_index or (
            self.usage.call_index if self.usage is not None else None
        )
        self.scope_task_ids = list(
            scope_task_ids
            if scope_task_ids is not None
            else self.usage.scope_task_ids
            if self.usage is not None
            else []
        )
        self.requested_model = requested_model or (
            self.usage.requested_model if self.usage is not None else None
        )


class ExtractorProviderError(RuntimeError):
    """Raised when a paid extraction response cannot be used safely."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        usage: AgentIterationUsage | None = None,
        usages: list[AgentIterationUsage] | None = None,
        agent: str = "extractor",
        iteration: int | None = None,
        call_index: int | None = None,
        scope_task_ids: list[str] | None = None,
        requested_model: str | None = None,
        source_id: str | None = None,
        failed_attempts: list[ExtractionAttemptFailure] | None = None,
    ):
        super().__init__(message)
        self.code = code
        collected_usages = list(usages or [])
        if usage is not None and usage not in collected_usages:
            collected_usages.append(usage)
        self.usages = collected_usages
        self.usage = usage or (collected_usages[-1] if collected_usages else None)
        self.agent = agent
        self.iteration = iteration or (
            self.usage.iteration if self.usage is not None else None
        )
        self.call_index = call_index or (
            self.usage.call_index if self.usage is not None else None
        )
        self.scope_task_ids = list(
            scope_task_ids
            if scope_task_ids is not None
            else self.usage.scope_task_ids
            if self.usage is not None
            else []
        )
        self.requested_model = requested_model or (
            self.usage.requested_model if self.usage is not None else None
        )
        self.source_id = source_id
        self.failed_attempts = list(failed_attempts or [])


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
        call_index: int,
        max_search_calls: int,
        min_queries_per_task: int,
    ) -> SearcherGeneration: ...


class ExtractorLLM(Protocol):
    @property
    def model_name(self) -> str: ...

    def generate(
        self,
        plan: ResearchPlan,
        source: SearchSource,
        document: SourceDocument,
        tasks: list[ResearchTask],
        passages: list[EvidencePassage],
        system_prompt: str,
        *,
        iteration: int,
        call_index: int,
    ) -> ExtractorGeneration: ...
