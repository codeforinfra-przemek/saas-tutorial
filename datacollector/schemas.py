"""Versioned contracts shared by the franchise research loop agents."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath
from string import Formatter
from typing import Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SCHEMA_VERSION = "1.2.0"
PROMPT_VERSION = "planner-system-v2"
SEARCHER_SCHEMA_VERSION = "1.1.0"
SEARCHER_PROMPT_VERSION = "searcher-system-v3"
EXTRACTOR_SCHEMA_VERSION = "1.0.0"
EXTRACTOR_PROMPT_VERSION = "extractor-system-v2"
CHECKER_SCHEMA_VERSION = "1.1.0"
CHECKER_PROMPT_VERSION = "checker-system-v2"
CHECKER_SCORING_VERSION = "checker-scoring-v2"
RESOLVER_SCHEMA_VERSION = "1.0.0"
RESOLVER_PROMPT_VERSION = "resolver-system-v1"


class ClosedModel(BaseModel):
    """A contract that rejects undeclared fields while allowing normal parsing."""

    model_config = ConfigDict(extra="forbid")


class ResearchDepth(StrEnum):
    CATALOG = "catalog"
    DUE_DILIGENCE = "due_diligence"
    RISK = "risk"
    UNIT = "unit"


DEPTH_ORDER = {
    ResearchDepth.CATALOG: 1,
    ResearchDepth.DUE_DILIGENCE: 2,
    ResearchDepth.RISK: 3,
    ResearchDepth.UNIT: 4,
}


class Requirement(StrEnum):
    CRITICAL = "critical"
    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class Priority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


PRIORITY_ORDER = {
    Priority.LOW: 1,
    Priority.MEDIUM: 2,
    Priority.HIGH: 3,
    Priority.CRITICAL: 4,
}


class TaskAction(StrEnum):
    COLLECT = "collect"
    VERIFY = "verify_existing"
    COLLECT_AND_VERIFY = "collect_missing_and_verify_existing"


class SourceType(StrEnum):
    OFFICIAL = "official"
    GOVERNMENT = "government"
    REGULATOR = "regulator"
    REGISTRY = "registry"
    ROUTING_LEAD = "routing_lead"
    COURT = "court"
    LEGAL_DOCUMENT = "legal_document"
    LEGISLATIVE_PROJECT = "legislative_project"
    AUDITED_FINANCIAL = "audited_financial"
    REPUTABLE_MEDIA = "reputable_media"
    INDUSTRY = "industry"
    BLOG = "blog"
    YOUTUBE = "youtube"
    MARKETPLACE = "marketplace"
    FRANCHISEE_INTERVIEW = "franchisee_interview"
    REVIEW_PLATFORM = "review_platform"
    SOCIAL = "social"
    UNKNOWN = "unknown"


class Sensitivity(StrEnum):
    PUBLIC_BUSINESS = "public_business"
    LEGAL = "legal"
    FINANCIAL = "financial"
    PERSONAL_DATA = "personal_data"
    OPINION = "opinion"


class Jurisdiction(StrEnum):
    ALL = "all"
    US_ONLY = "us_only"


class EvidenceRule(ClosedModel):
    min_sources: int = Field(ge=1, le=10)
    preferred_source_types: list[SourceType] = Field(min_length=1)
    acceptance_criteria: str = Field(min_length=10)
    requires_independent_corroboration: bool = False
    max_age_days: int | None = Field(default=None, ge=1)


class CatalogQuestion(ClosedModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    title: str = Field(min_length=3)
    question: str = Field(min_length=10)
    fdd_items: list[int] = Field(default_factory=list)
    minimum_depth: ResearchDepth
    requirement: Requirement
    target_fields: list[str] = Field(min_length=1)
    evidence: EvidenceRule
    search_query_templates: list[str] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    sensitivity: Sensitivity = Sensitivity.PUBLIC_BUSINESS
    jurisdiction: Jurisdiction = Jurisdiction.ALL
    tags: list[str] = Field(default_factory=list)

    @field_validator("fdd_items")
    @classmethod
    def validate_fdd_items(cls, values: list[int]) -> list[int]:
        if any(value < 1 or value > 23 for value in values):
            raise ValueError("FDD item numbers must be between 1 and 23.")
        if len(values) != len(set(values)):
            raise ValueError("FDD item numbers must be unique per question.")
        return values

    @field_validator("target_fields", "dependencies")
    @classmethod
    def validate_unique_strings(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("List values must be unique.")
        return values

    @field_validator("search_query_templates")
    @classmethod
    def validate_query_templates(cls, values: list[str]) -> list[str]:
        allowed_fields = {"brand", "country", "regions", "legal_name"}
        for template in values:
            try:
                parsed_fields = [
                    field_name
                    for _, field_name, _, _ in Formatter().parse(template)
                    if field_name is not None
                ]
            except ValueError as exc:
                raise ValueError(f"Invalid search query template: {template!r}.") from exc
            unknown_fields = set(parsed_fields) - allowed_fields
            if unknown_fields:
                raise ValueError(
                    "Search query templates contain unsupported placeholders: "
                    f"{sorted(unknown_fields)}."
                )
        return values


class CatalogSection(ClosedModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    title: str = Field(min_length=3)
    framework: str = Field(min_length=3)
    questions: list[CatalogQuestion] = Field(min_length=1)


class SourcePolicy(ClosedModel):
    preferred_order: list[SourceType] = Field(min_length=1)
    rules: list[str] = Field(min_length=1)
    prohibited_methods: list[str] = Field(min_length=1)


class QuestionCatalog(ClosedModel):
    version: str = Field(min_length=1)
    title: str = Field(min_length=3)
    legal_note: str = Field(min_length=20)
    authoritative_sources: list[str] = Field(min_length=1)
    source_policy: SourcePolicy
    sections: list[CatalogSection] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_question_graph(self) -> "QuestionCatalog":
        section_ids = [section.id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Catalog section IDs must be unique.")

        questions = self.all_questions()
        question_ids = [question.id for question in questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Catalog question IDs must be unique.")

        known_ids = set(question_ids)
        graph: dict[str, list[str]] = {}
        for question in questions:
            unknown = set(question.dependencies) - known_ids
            if unknown:
                raise ValueError(
                    f"Question {question.id} has unknown dependencies: {sorted(unknown)}"
                )
            graph[question.id] = question.dependencies

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(question_id: str) -> None:
            if question_id in visiting:
                raise ValueError(
                    f"Catalog question dependency cycle contains: {question_id}"
                )
            if question_id in visited:
                return
            visiting.add(question_id)
            for dependency in graph[question_id]:
                visit(dependency)
            visiting.remove(question_id)
            visited.add(question_id)

        for question_id in graph:
            visit(question_id)
        return self

    def all_questions(self) -> list[CatalogQuestion]:
        return [question for section in self.sections for question in section.questions]


class PlannerInput(ClosedModel):
    brand_name: str = Field(min_length=1, max_length=200)
    target_country: str = Field(default="PL", pattern=r"^[A-Z]{2}$")
    target_regions: list[str] = Field(default_factory=list)
    research_languages: list[str] = Field(default_factory=lambda: ["pl", "en"])
    depth: ResearchDepth = ResearchDepth.DUE_DILIGENCE
    known_legal_name: str | None = Field(default=None, max_length=300)
    known_official_website: str | None = Field(default=None, max_length=1000)
    existing_fields: list[str] = Field(default_factory=list)
    max_queries_per_task: int = Field(default=3, ge=1, le=10)
    quality_threshold: int = Field(default=80, ge=0, le=100)
    max_rounds: int = Field(default=3, ge=1, le=10)
    allow_personal_data: bool = False

    @field_validator("brand_name")
    @classmethod
    def normalize_brand(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("brand_name cannot be blank.")
        return value

    @field_validator("target_country", mode="before")
    @classmethod
    def normalize_country(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("target_country must be a two-letter country code.")
        return value.strip().upper()

    @field_validator(
        "target_regions", "research_languages", "existing_fields", mode="after"
    )
    @classmethod
    def unique_ordered_values(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        return list(dict.fromkeys(cleaned))


class PlannerTaskGuidance(ClosedModel):
    catalog_question_id: str
    priority: Priority
    rationale: str = Field(min_length=5)
    search_queries: list[str] = Field(default_factory=list, max_length=3)
    source_hints: list[str] = Field(default_factory=list, max_length=3)


class PlannerDraft(ClosedModel):
    """Small structured response produced by the LLM.

    Canonical tasks never come from the LLM. The model can only add planning
    guidance, which is merged into the deterministic catalog by PlannerAgent.
    """

    objective: str = Field(min_length=10)
    planning_notes: list[str] = Field(default_factory=list, max_length=12)
    assumptions: list[str] = Field(default_factory=list, max_length=12)
    scope_warnings: list[str] = Field(default_factory=list, max_length=12)
    task_guidance: list[PlannerTaskGuidance] = Field(
        default_factory=list, max_length=25
    )

    @model_validator(mode="after")
    def validate_guidance_ids(self) -> "PlannerDraft":
        ids = [item.catalog_question_id for item in self.task_guidance]
        if len(ids) != len(set(ids)):
            raise ValueError("task_guidance contains duplicate catalog_question_id values.")
        return self


class ResearchTask(ClosedModel):
    task_id: str
    catalog_question_id: str
    section_id: str
    title: str
    question: str
    fdd_items: list[int]
    priority: Priority
    requirement: Requirement
    action: TaskAction
    target_fields: list[str] = Field(min_length=1)
    fields_to_collect: list[str]
    fields_to_verify: list[str]
    preferred_source_types: list[SourceType]
    source_hints: list[str]
    search_queries: list[str]
    acceptance_criteria: str
    min_sources: int
    requires_independent_corroboration: bool
    max_age_days: int | None
    depends_on: list[str]
    sensitivity: Sensitivity
    rationale: str

    @model_validator(mode="after")
    def validate_field_work(self) -> "ResearchTask":
        for name, values in (
            ("target_fields", self.target_fields),
            ("fields_to_collect", self.fields_to_collect),
            ("fields_to_verify", self.fields_to_verify),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} values must be unique.")

        collect = set(self.fields_to_collect)
        verify = set(self.fields_to_verify)
        targets = set(self.target_fields)
        if collect & verify:
            raise ValueError("A field cannot be both collected and verified.")
        if collect | verify != targets:
            raise ValueError(
                "fields_to_collect and fields_to_verify must partition target_fields."
            )
        if collect and verify:
            expected_action = TaskAction.COLLECT_AND_VERIFY
        elif collect:
            expected_action = TaskAction.COLLECT
        else:
            expected_action = TaskAction.VERIFY
        if self.action != expected_action:
            raise ValueError(
                f"Task action {self.action} does not match its field work split."
            )
        return self


class StopConditions(ClosedModel):
    quality_threshold: int = Field(ge=0, le=100)
    max_rounds: int = Field(ge=1, le=10)
    no_critical_missing: bool = True
    human_review_required: bool = True


class TokenUsage(ClosedModel):
    """Actual token counts reported by one completed provider response."""

    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    cache_write_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_token_totals(self) -> "TokenUsage":
        if (
            self.cached_input_tokens + self.cache_write_input_tokens
            > self.input_tokens
        ):
            raise ValueError(
                "cached and cache-write input tokens cannot exceed input_tokens."
            )
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning_tokens cannot exceed output_tokens.")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens plus output_tokens.")
        return self


class ToolUsage(ClosedModel):
    """Observed separately billed tool calls and their dated price estimate."""

    tool: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    calls: int = Field(ge=0)
    action_counts: dict[str, int] = Field(default_factory=dict)
    unit_cost_usd: Decimal = Field(ge=0)
    estimated_cost_usd: Decimal = Field(ge=0)
    pricing_source: str = Field(min_length=1)
    pricing_as_of: date

    @model_validator(mode="after")
    def validate_tool_cost(self) -> "ToolUsage":
        if any(count < 0 for count in self.action_counts.values()):
            raise ValueError("Tool action counts cannot be negative.")
        if self.action_counts and sum(self.action_counts.values()) != self.calls:
            raise ValueError("Tool action counts must sum to calls.")
        if self.estimated_cost_usd != self.unit_cost_usd * self.calls:
            raise ValueError("Tool estimated cost must equal unit cost times calls.")
        return self


class CostEstimate(ClosedModel):
    """Auditable estimate calculated from a dated public rate card."""

    currency: Literal["USD"] = "USD"
    rate_card_id: str
    pricing_source: str
    pricing_as_of: date
    input_usd_per_million: Decimal = Field(ge=0)
    cached_input_usd_per_million: Decimal = Field(ge=0)
    cache_write_usd_per_million: Decimal = Field(ge=0)
    output_usd_per_million: Decimal = Field(ge=0)
    uncached_input_cost_usd: Decimal = Field(ge=0)
    cached_input_cost_usd: Decimal = Field(ge=0)
    cache_write_input_cost_usd: Decimal = Field(ge=0)
    output_cost_usd: Decimal = Field(ge=0)
    tool_cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    total_estimated_cost_usd: Decimal = Field(ge=0)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cost_total(self) -> "CostEstimate":
        expected = (
            self.uncached_input_cost_usd
            + self.cached_input_cost_usd
            + self.cache_write_input_cost_usd
            + self.output_cost_usd
            + self.tool_cost_usd
        )
        if self.total_estimated_cost_usd != expected:
            raise ValueError("total_estimated_cost_usd must equal its components.")
        return self


class AgentIterationUsage(ClosedModel):
    """Usage and estimated cost for one logical iteration of one agent."""

    agent: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    iteration: int = Field(ge=1)
    call_index: int = Field(default=1, ge=1)
    scope_task_ids: list[str] = Field(default_factory=list)
    scope_source_ids: list[str] = Field(default_factory=list)
    provider: Literal["openai"] = "openai"
    requested_model: str = Field(min_length=1)
    resolved_model: str = Field(min_length=1)
    response_id: str | None = None
    request_id: str | None = None
    service_tier: str | None = None
    tokens: TokenUsage
    tool_usage: list[ToolUsage] = Field(default_factory=list)
    cost_estimate: CostEstimate | None = None

    @model_validator(mode="after")
    def validate_usage_scope(self) -> "AgentIterationUsage":
        if len(self.scope_task_ids) != len(set(self.scope_task_ids)):
            raise ValueError("Usage scope_task_ids values must be unique.")
        if len(self.scope_source_ids) != len(set(self.scope_source_ids)):
            raise ValueError("Usage scope_source_ids values must be unique.")
        if self.cost_estimate is not None:
            recorded_tool_cost = sum(
                (item.estimated_cost_usd for item in self.tool_usage),
                start=Decimal("0"),
            )
            if self.cost_estimate.tool_cost_usd != recorded_tool_cost:
                raise ValueError(
                    "Cost estimate tool cost must match recorded tool usage."
                )
        return self


class AgentFailureArtifact(ClosedModel):
    """Known cost facts for a provider response that could not be used."""

    schema_version: Literal["1.0.0", "1.1.0"] = "1.1.0"
    failure_id: str
    plan_run_id: str
    created_at: datetime
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    agent: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]*$")
    iteration: int | None = Field(default=None, ge=1)
    call_index: int | None = Field(default=None, ge=1)
    scope_task_ids: list[str] = Field(default_factory=list)
    scope_source_ids: list[str] = Field(default_factory=list)
    provider: Literal["openai"] = "openai"
    requested_model: str | None = None
    usage: AgentIterationUsage | None = None
    observed_tool_calls: int = Field(default=0, ge=0)
    tool_usage: list[ToolUsage] = Field(default_factory=list)
    token_usage_unknown: bool = False

    @model_validator(mode="after")
    def validate_failure_ids(self) -> "AgentFailureArtifact":
        for value, field_name in (
            (self.failure_id, "failure_id"),
            (self.plan_run_id, "plan_run_id"),
        ):
            try:
                parsed = UUID(value)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"{field_name} must be a valid UUIDv4.") from exc
            if parsed.version != 4:
                raise ValueError(f"{field_name} must be a valid UUIDv4.")
        if self.schema_version == "1.0.0":
            if self.usage is None:
                raise ValueError("Schema 1.0 failure artifacts require usage.")
            return self
        if (
            self.agent is None
            or self.iteration is None
            or self.call_index is None
            or self.requested_model is None
            or not self.requested_model.strip()
        ):
            raise ValueError(
                "Schema 1.1 failure artifacts require agent, call, and model metadata."
            )
        if len(self.scope_task_ids) != len(set(self.scope_task_ids)):
            raise ValueError("Failure artifact task scope must be unique.")
        if len(self.scope_source_ids) != len(set(self.scope_source_ids)):
            raise ValueError("Failure artifact source scope must be unique.")
        if any(
            not re.fullmatch(r"source-[a-f0-9]{16}", source_id)
            for source_id in self.scope_source_ids
        ):
            raise ValueError("Failure artifact source scope has an invalid ID.")
        if self.agent == "extractor" and len(self.scope_source_ids) != 1:
            raise ValueError("Extractor failure artifacts require one source ID.")
        billed_tool_calls = sum(item.calls for item in self.tool_usage)
        if billed_tool_calls > self.observed_tool_calls:
            raise ValueError(
                "Observed tool calls cannot be lower than billed tool usage."
            )
        if self.usage is None:
            if not self.token_usage_unknown:
                raise ValueError(
                    "A failure without provider usage must mark tokens unknown."
                )
        else:
            if self.token_usage_unknown:
                raise ValueError(
                    "A failure with provider usage cannot mark tokens unknown."
                )
            if (
                self.agent != self.usage.agent
                or self.iteration != self.usage.iteration
                or self.call_index != self.usage.call_index
                or self.scope_task_ids != self.usage.scope_task_ids
                or self.scope_source_ids != self.usage.scope_source_ids
                or self.provider != self.usage.provider
                or self.requested_model != self.usage.requested_model
            ):
                raise ValueError(
                    "Failure metadata must match its provider usage entry."
                )
            if self.tool_usage != self.usage.tool_usage:
                raise ValueError(
                    "Failure tool usage must match its provider usage entry."
                )
        return self


class ResearchPlan(ClosedModel):
    schema_version: Literal["1.0.0", "1.1.0", "1.2.0"] = SCHEMA_VERSION
    catalog_version: str
    prompt_version: str = PROMPT_VERSION
    run_id: str
    created_at: datetime
    generated_by: Literal["offline", "openai"]
    model: str | None
    planner_input: PlannerInput
    objective: str
    planning_notes: list[str]
    assumptions: list[str]
    scope_warnings: list[str]
    tasks: list[ResearchTask] = Field(min_length=1)
    critical_fields: list[str]
    stop_conditions: StopConditions
    authoritative_sources: list[str]
    source_policy: SourcePolicy
    compliance_rules: list[str]
    agent_usage: list[AgentIterationUsage] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_plan(self) -> "ResearchPlan":
        try:
            parsed_run_id = UUID(self.run_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError("run_id must be a valid UUIDv4.") from exc
        if parsed_run_id.version != 4:
            raise ValueError("run_id must be a valid UUIDv4.")

        if self.generated_by == "offline" and self.model is not None:
            raise ValueError("Offline plans cannot declare an OpenAI model.")
        if self.generated_by == "openai" and (
            self.model is None or not self.model.strip()
        ):
            raise ValueError("OpenAI-generated plans must declare a model.")
        if self.generated_by == "offline" and self.agent_usage:
            raise ValueError("Offline plans cannot contain provider usage.")
        if (
            self.schema_version in {"1.1.0", "1.2.0"}
            and self.generated_by == "openai"
            and not self.agent_usage
        ):
            raise ValueError("OpenAI-generated schema 1.1 plans must contain usage.")

        usage_keys = [
            (item.agent, item.iteration, item.call_index)
            for item in self.agent_usage
        ]
        if len(usage_keys) != len(set(usage_keys)):
            raise ValueError(
                "Agent usage entries must be unique per agent iteration and call."
            )

        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Research task IDs must be unique.")
        question_ids = [task.catalog_question_id for task in self.tasks]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Each catalog question may produce only one task.")

        known_task_ids = set(task_ids)
        graph: dict[str, list[str]] = {}
        for task in self.tasks:
            unknown = set(task.depends_on) - known_task_ids
            if unknown:
                raise ValueError(
                    f"Task {task.task_id} has unknown dependencies: {sorted(unknown)}"
                )
            graph[task.task_id] = task.depends_on

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError(f"Research task dependency cycle contains: {task_id}")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)

        if len(self.critical_fields) != len(set(self.critical_fields)):
            raise ValueError("critical_fields values must be unique.")
        expected_critical_fields = {
            field
            for task in self.tasks
            if task.priority == Priority.CRITICAL
            for field in task.target_fields
        }
        if set(self.critical_fields) != expected_critical_fields:
            raise ValueError(
                "critical_fields must equal the fields targeted by critical tasks."
            )
        return self


class SearchTaskStatus(StrEnum):
    QUERY_WORKLOAD_ONLY = "query_workload_only"
    SOURCES_FOUND = "sources_found"
    PARTIAL = "partial"
    NO_SOURCES_FOUND = "no_sources_found"
    NOT_SEARCHED = "not_searched"


class SearchQueryCoverage(StrEnum):
    LEGACY_UNKNOWN = "legacy_unknown"
    WORKLOAD_ONLY = "workload_only"
    NONE = "none"
    PARTIAL = "partial"
    COMPLETE = "complete"


class SearchSourceOrigin(StrEnum):
    PLAN_SEED = "plan_seed"
    OPENAI_WEB_SEARCH = "openai_web_search"


class SearcherSourceDraft(ClosedModel):
    """Model-proposed mapping; URLs are trusted only after provider validation."""

    url: str = Field(min_length=8, max_length=4000)
    title: str = Field(default="", max_length=500)
    source_type: SourceType = SourceType.UNKNOWN
    task_ids: list[str] = Field(default_factory=list, max_length=50)
    relevance_note: str = Field(default="", max_length=1000)


class SearcherTaskDraft(ClosedModel):
    task_id: str
    status: SearchTaskStatus
    attempted_queries: list[str] = Field(default_factory=list, max_length=20)
    source_urls: list[str] = Field(default_factory=list, max_length=30)
    unresolved_targets: list[str] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=1000)


class SearcherDraft(ClosedModel):
    """Structured Searcher response before deterministic provenance checks."""

    warnings: list[str] = Field(default_factory=list, max_length=20)
    sources: list[SearcherSourceDraft] = Field(default_factory=list, max_length=100)
    task_results: list[SearcherTaskDraft] = Field(
        default_factory=list, max_length=50
    )

    @model_validator(mode="after")
    def validate_draft_uniqueness(self) -> "SearcherDraft":
        task_ids = [result.task_id for result in self.task_results]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Searcher task_results contains duplicate task IDs.")
        return self


class SearchLimits(ClosedModel):
    max_search_calls: int = Field(ge=1, le=100)
    task_limit: int | None = Field(default=None, ge=1)
    requested_task_ids: list[str] = Field(default_factory=list)
    min_queries_per_task: int = Field(default=1, ge=1, le=20)
    max_retry_tasks: int = Field(default=0, ge=0, le=50)
    retry_search_calls: int = Field(default=1, ge=1, le=10)


class SearchAction(ClosedModel):
    action_id: str | None = None
    call_index: int = Field(default=1, ge=1)
    scope_task_ids: list[str] = Field(default_factory=list)
    action_type: str = Field(min_length=1, max_length=100)
    status: str = Field(default="completed", min_length=1, max_length=100)
    queries: list[str] = Field(default_factory=list)
    target_url: str | None = None
    source_urls: list[str] = Field(default_factory=list)


class SearchSource(ClosedModel):
    source_id: str = Field(pattern=r"^source-[a-f0-9]{16}$")
    url: str = Field(min_length=8, max_length=4000)
    canonical_url: str = Field(min_length=8, max_length=4000)
    title: str = Field(default="", max_length=500)
    source_type: SourceType = SourceType.UNKNOWN
    origin: SearchSourceOrigin
    provider_observed: bool = Field(
        validation_alias=AliasChoices("provider_observed", "provider_verified")
    )
    task_ids: list[str] = Field(default_factory=list)
    observed_in_action_ids: list[str] = Field(default_factory=list)
    discovered_via_queries: list[str] = Field(default_factory=list)
    relevance_note: str = Field(default="", max_length=1000)
    discovered_at: datetime

    @property
    def provider_verified(self) -> bool:
        """Compatibility accessor for code reading schema 1.0 artifacts."""

        return self.provider_observed


class SearchTaskResult(ClosedModel):
    task_id: str
    catalog_question_id: str
    status: SearchTaskStatus
    planned_queries: list[str]
    attempted_queries: list[str]
    planned_queries_attempted: list[str] = Field(default_factory=list)
    derived_queries_attempted: list[str] = Field(default_factory=list)
    query_coverage: SearchQueryCoverage = SearchQueryCoverage.LEGACY_UNKNOWN
    minimum_query_attempts: int = Field(default=0, ge=0)
    minimum_sources: int = Field(default=0, ge=0)
    action_ids: list[str] = Field(default_factory=list)
    source_ids: list[str]
    coverage_gaps: list[str] = Field(default_factory=list)
    unresolved_targets: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=1000)


class SearchAttemptFailure(ClosedModel):
    """A non-fatal paid retry that could not be used in the final result."""

    call_index: int = Field(ge=2)
    scope_task_ids: list[str] = Field(min_length=1)
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    usage_recorded: bool
    observed_tool_calls: int = Field(default=0, ge=0)
    tool_usage: list[ToolUsage] = Field(default_factory=list)
    token_usage_unknown: bool = False


class SearchResults(ClosedModel):
    """Auditable source-discovery artifact consumed later by Extractor."""

    schema_version: Literal["1.0.0", "1.1.0"] = SEARCHER_SCHEMA_VERSION
    prompt_version: str = SEARCHER_PROMPT_VERSION
    search_id: str
    plan_run_id: str
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_reference: str = Field(min_length=1)
    created_at: datetime
    iteration: int = Field(ge=1)
    generated_by: Literal["offline", "openai"]
    model: str | None
    brand_name: str
    target_country: str = Field(pattern=r"^[A-Z]{2}$")
    depth: ResearchDepth
    search_executed: bool
    limits: SearchLimits
    selected_task_ids: list[str] = Field(min_length=1)
    unselected_task_ids: list[str]
    actions: list[SearchAction]
    sources: list[SearchSource]
    task_results: list[SearchTaskResult] = Field(min_length=1)
    warnings: list[str]
    compliance_rules: list[str]
    agent_usage: list[AgentIterationUsage] = Field(default_factory=list)
    failed_attempts: list[SearchAttemptFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_search_results(self) -> "SearchResults":
        for value, field_name in (
            (self.search_id, "search_id"),
            (self.plan_run_id, "plan_run_id"),
        ):
            try:
                parsed = UUID(value)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"{field_name} must be a valid UUIDv4.") from exc
            if parsed.version != 4:
                raise ValueError(f"{field_name} must be a valid UUIDv4.")

        if len(self.selected_task_ids) != len(set(self.selected_task_ids)):
            raise ValueError("selected_task_ids values must be unique.")
        if len(self.unselected_task_ids) != len(set(self.unselected_task_ids)):
            raise ValueError("unselected_task_ids values must be unique.")
        if set(self.selected_task_ids) & set(self.unselected_task_ids):
            raise ValueError("Selected and unselected tasks cannot overlap.")

        result_task_ids = [result.task_id for result in self.task_results]
        if result_task_ids != self.selected_task_ids:
            raise ValueError(
                "task_results must follow and exactly cover selected_task_ids."
            )

        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Search source IDs must be unique.")
        known_sources = set(source_ids)
        known_tasks = set(self.selected_task_ids)
        action_ids = [action.action_id for action in self.actions]
        populated_action_ids = [item for item in action_ids if item is not None]
        if len(populated_action_ids) != len(set(populated_action_ids)):
            raise ValueError("Search action IDs must be unique when present.")
        if self.schema_version == "1.1.0" and len(populated_action_ids) != len(
            self.actions
        ):
            raise ValueError("Schema 1.1 search actions require stable action IDs.")
        known_actions = set(populated_action_ids)
        action_by_id = {
            action.action_id: action
            for action in self.actions
            if action.action_id is not None
        }
        for action in self.actions:
            if len(action.scope_task_ids) != len(set(action.scope_task_ids)):
                raise ValueError("Search action scope task IDs must be unique.")
            if len(action.queries) != len(set(action.queries)):
                raise ValueError("Search action queries must be unique.")
            if len(action.source_urls) != len(set(action.source_urls)):
                raise ValueError("Search action source URLs must be unique.")
            if not set(action.scope_task_ids).issubset(known_tasks):
                raise ValueError(
                    "Search action scopes may reference only selected tasks."
                )
        source_by_id = {source.source_id: source for source in self.sources}
        for source in self.sources:
            if len(source.task_ids) != len(set(source.task_ids)):
                raise ValueError("Search source task IDs must be unique.")
            if len(source.discovered_via_queries) != len(
                set(source.discovered_via_queries)
            ):
                raise ValueError("Search source query provenance must be unique.")
            if not set(source.task_ids).issubset(known_tasks):
                raise ValueError("Search sources may reference only selected tasks.")
            if (
                self.schema_version == "1.1.0"
                and source.url != source.canonical_url
            ):
                raise ValueError(
                    "Schema 1.1 source URL must equal its canonical URL."
                )
            if len(source.observed_in_action_ids) != len(
                set(source.observed_in_action_ids)
            ):
                raise ValueError("Search source action IDs must be unique.")
            if not set(source.observed_in_action_ids).issubset(known_actions):
                raise ValueError("Search sources reference unknown action IDs.")
            if source.provider_observed != (
                source.origin == SearchSourceOrigin.OPENAI_WEB_SEARCH
            ):
                raise ValueError(
                    "Search source origin must match provider observation status."
                )
            if (
                self.schema_version == "1.1.0"
                and source.origin == SearchSourceOrigin.OPENAI_WEB_SEARCH
                and (not source.task_ids or not source.observed_in_action_ids)
            ):
                raise ValueError(
                    "Schema 1.1 provider sources must be mapped to tasks and actions."
                )
            if self.schema_version == "1.1.0" and source.observed_in_action_ids:
                observed_actions = [
                    action_by_id[action_id]
                    for action_id in source.observed_in_action_ids
                ]
                if any(action.status != "completed" for action in observed_actions):
                    raise ValueError(
                        "Source provenance may reference only completed actions."
                    )
                if any(
                    source.canonical_url
                    not in {
                        *action.source_urls,
                        *([action.target_url] if action.target_url else []),
                    }
                    for action in observed_actions
                ):
                    raise ValueError(
                        "Source provenance actions must contain the source URL."
                    )
                if any(
                    not set(action.scope_task_ids).intersection(source.task_ids)
                    for action in observed_actions
                ):
                    raise ValueError(
                        "Each source provenance action must share a mapped task."
                    )
                if any(
                    not any(
                        task_id in action.scope_task_ids
                        for action in observed_actions
                    )
                    for task_id in source.task_ids
                ):
                    raise ValueError(
                        "Every source task must be covered by a provenance action."
                    )
                unambiguous_queries = {
                    action.queries[0]
                    for action in observed_actions
                    if len(action.queries) == 1
                }
                if not set(source.discovered_via_queries).issubset(
                    unambiguous_queries
                ):
                    raise ValueError(
                        "Source query provenance must come from a single-query "
                        "observed action."
                    )
        for result in self.task_results:
            for values, field_name in (
                (result.planned_queries, "planned_queries"),
                (result.attempted_queries, "attempted_queries"),
                (result.planned_queries_attempted, "planned_queries_attempted"),
                (result.derived_queries_attempted, "derived_queries_attempted"),
                (result.action_ids, "action_ids"),
                (result.source_ids, "source_ids"),
                (result.coverage_gaps, "coverage_gaps"),
                (result.unresolved_targets, "unresolved_targets"),
            ):
                if len(values) != len(set(values)):
                    raise ValueError(f"Task result {field_name} values must be unique.")
            if not set(result.source_ids).issubset(known_sources):
                raise ValueError("Task results reference unknown source IDs.")
            if not set(result.action_ids).issubset(known_actions):
                raise ValueError("Task results reference unknown action IDs.")
            if not set(result.planned_queries_attempted).issubset(
                set(result.planned_queries)
            ):
                raise ValueError(
                    "planned_queries_attempted must be a subset of planned_queries."
                )
            if not set(result.planned_queries_attempted).issubset(
                set(result.attempted_queries)
            ):
                raise ValueError(
                    "planned_queries_attempted must be a subset of attempted_queries."
                )
            if not set(result.derived_queries_attempted).issubset(
                set(result.attempted_queries)
            ):
                raise ValueError(
                    "derived_queries_attempted must be a subset of attempted_queries."
                )
            if set(result.planned_queries_attempted) & set(
                result.derived_queries_attempted
            ):
                raise ValueError(
                    "Planned and derived attempted queries cannot overlap."
                )
            if result.status in {
                SearchTaskStatus.SOURCES_FOUND,
                SearchTaskStatus.PARTIAL,
            } and not result.source_ids:
                raise ValueError(
                    "sources_found and partial task results require source IDs."
                )
            if result.status == SearchTaskStatus.NO_SOURCES_FOUND and (
                not result.attempted_queries or result.source_ids
            ):
                raise ValueError(
                    "no_sources_found requires attempted queries and no sources."
                )
            if result.status == SearchTaskStatus.NOT_SEARCHED and (
                result.attempted_queries
                or result.action_ids
                or result.source_ids
            ):
                raise ValueError(
                    "not_searched cannot contain attempts, actions, or sources."
                )
            if result.status == SearchTaskStatus.QUERY_WORKLOAD_ONLY and (
                result.attempted_queries
                or result.source_ids
                or result.action_ids
            ):
                raise ValueError(
                    "query_workload_only cannot contain attempts or sources."
                )
            for source_id in result.source_ids:
                if result.task_id not in source_by_id[source_id].task_ids:
                    raise ValueError(
                        "Task/source mappings must be symmetric in search results."
                    )
            if self.schema_version == "1.1.0":
                if any(
                    action_by_id[action_id].status != "completed"
                    or result.task_id
                    not in action_by_id[action_id].scope_task_ids
                    for action_id in result.action_ids
                ):
                    raise ValueError(
                        "Task results may reference only completed actions in "
                        "their task scope."
                    )
                referenced_action_queries = {
                    query
                    for action_id in result.action_ids
                    for query in action_by_id[action_id].queries
                }
                if not set(result.attempted_queries).issubset(
                    referenced_action_queries
                ):
                    raise ValueError(
                        "Task attempted queries must occur in its referenced actions."
                    )
                if result.query_coverage == SearchQueryCoverage.LEGACY_UNKNOWN:
                    raise ValueError(
                        "Schema 1.1 task results require explicit query coverage."
                    )
                expected_minimum = min(
                    result.minimum_query_attempts,
                    len(result.planned_queries),
                )
                planned_attempt_count = len(result.planned_queries_attempted)
                expected_coverage = (
                    SearchQueryCoverage.WORKLOAD_ONLY
                    if result.status == SearchTaskStatus.QUERY_WORKLOAD_ONLY
                    else SearchQueryCoverage.NONE
                    if planned_attempt_count == 0
                    else SearchQueryCoverage.COMPLETE
                    if planned_attempt_count >= expected_minimum
                    else SearchQueryCoverage.PARTIAL
                )
                if result.query_coverage != expected_coverage:
                    raise ValueError(
                        "Task query_coverage does not match confirmed planned queries."
                    )
                if result.status == SearchTaskStatus.SOURCES_FOUND and (
                    result.query_coverage != SearchQueryCoverage.COMPLETE
                    or len(result.source_ids) < result.minimum_sources
                    or result.coverage_gaps
                    or result.unresolved_targets
                ):
                    raise ValueError(
                        "sources_found requires complete minimum Searcher coverage."
                    )
                if result.status == SearchTaskStatus.PARTIAL and (
                    not result.source_ids
                    or (
                        result.query_coverage == SearchQueryCoverage.COMPLETE
                        and len(result.source_ids) >= result.minimum_sources
                        and not result.coverage_gaps
                        and not result.unresolved_targets
                    )
                ):
                    raise ValueError(
                        "partial requires sources plus an explicit coverage gap."
                    )
        result_by_task = {result.task_id: result for result in self.task_results}
        for source in self.sources:
            for task_id in source.task_ids:
                if source.source_id not in result_by_task[task_id].source_ids:
                    raise ValueError(
                        "Source/task mappings must be symmetric in search results."
                    )

        usage_keys = [
            (usage.agent, usage.iteration, usage.call_index)
            for usage in self.agent_usage
        ]
        if len(usage_keys) != len(set(usage_keys)):
            raise ValueError(
                "Agent usage entries must be unique per agent iteration and call."
            )
        failed_call_indices = [item.call_index for item in self.failed_attempts]
        if len(failed_call_indices) != len(set(failed_call_indices)):
            raise ValueError("Failed Searcher call indices must be unique.")
        usage_call_indices = {usage.call_index for usage in self.agent_usage}
        for failure in self.failed_attempts:
            if not set(failure.scope_task_ids).issubset(known_tasks):
                raise ValueError(
                    "Failed Searcher attempts may reference only selected tasks."
                )
            if failure.usage_recorded != (
                failure.call_index in usage_call_indices
            ):
                raise ValueError(
                    "Failed Searcher attempt usage flag must match the usage ledger."
                )
            if sum(item.calls for item in failure.tool_usage) > (
                failure.observed_tool_calls
            ):
                raise ValueError(
                    "Failed attempt observed tool calls cannot be lower than "
                    "billed tool usage."
                )
            if failure.usage_recorded:
                failed_usage = next(
                    usage
                    for usage in self.agent_usage
                    if usage.call_index == failure.call_index
                )
                recorded_failed_search_calls = sum(
                    tool.calls
                    for tool in failed_usage.tool_usage
                    if tool.tool == "web_search"
                )
                if recorded_failed_search_calls > failure.observed_tool_calls:
                    raise ValueError(
                        "Failed attempt tool-call count cannot be lower than its "
                        "recorded search calls."
                    )
                if failure.tool_usage != failed_usage.tool_usage:
                    raise ValueError(
                        "Failed attempt tool usage must match its usage ledger entry."
                    )
                if failure.token_usage_unknown:
                    raise ValueError(
                        "Failed attempt with usage cannot mark tokens unknown."
                    )
            elif not failure.token_usage_unknown:
                raise ValueError(
                    "Failed attempt without usage must mark tokens unknown."
                )

        if self.generated_by == "offline":
            if (
                self.model is not None
                or self.agent_usage
                or self.failed_attempts
                or self.search_executed
            ):
                raise ValueError(
                    "Free Searcher cannot declare a model, provider attempts, "
                    "usage, or executed search."
                )
            if self.actions or any(
                source.provider_observed for source in self.sources
            ):
                raise ValueError(
                    "Free Searcher cannot contain provider actions or observed "
                    "provider sources."
                )
            if any(
                result.status != SearchTaskStatus.QUERY_WORKLOAD_ONLY
                for result in self.task_results
            ):
                raise ValueError(
                    "Free Searcher task results must be query workloads only."
                )
        else:
            if self.model is None or not self.model.strip():
                raise ValueError("OpenAI Searcher must declare a model.")
            if not self.search_executed or not self.agent_usage:
                raise ValueError(
                    "OpenAI Searcher must record executed search and provider usage."
                )
            if not self.actions or not any(
                action.action_type == "search" and action.status == "completed"
                for action in self.actions
            ):
                raise ValueError(
                    "OpenAI Searcher must contain a completed search action."
                )
            if len(self.actions) > self.limits.max_search_calls:
                raise ValueError("Search actions exceed the configured tool-call cap.")
            if any(
                usage.agent != "searcher" or usage.iteration != self.iteration
                for usage in self.agent_usage
            ):
                raise ValueError(
                    "Search usage must belong to this Searcher iteration."
                )
            if any(
                usage.scope_task_ids
                and not set(usage.scope_task_ids).issubset(known_tasks)
                for usage in self.agent_usage
            ):
                raise ValueError(
                    "Search usage scopes may reference only selected tasks."
                )
            observed_search_calls = sum(
                action.action_type == "search" for action in self.actions
            )
            action_call_indices = {action.call_index for action in self.actions}
            if not action_call_indices.issubset(usage_call_indices):
                raise ValueError(
                    "Every recorded Searcher action must have a usage entry."
                )
            usage_by_call_index = {
                usage.call_index: usage for usage in self.agent_usage
            }
            if self.schema_version == "1.1.0" and any(
                set(action.scope_task_ids)
                != set(usage_by_call_index[action.call_index].scope_task_ids)
                for action in self.actions
            ):
                raise ValueError(
                    "Search action scope must match usage scope for its call."
                )
            if not usage_call_indices.issubset(
                action_call_indices | set(failed_call_indices)
            ):
                raise ValueError(
                    "Every Searcher usage entry must belong to actions or a "
                    "recorded failed attempt."
                )
            recorded_search_calls = sum(
                tool.calls
                for usage in self.agent_usage
                if usage.call_index not in set(failed_call_indices)
                for tool in usage.tool_usage
                if tool.tool == "web_search"
            )
            if observed_search_calls != recorded_search_calls:
                raise ValueError(
                    "Recorded web search tool calls must match search actions."
                )
            failed_tool_calls = sum(
                failure.observed_tool_calls for failure in self.failed_attempts
            )
            if len(self.actions) + failed_tool_calls > self.limits.max_search_calls:
                raise ValueError(
                    "Successful actions and observed failed tool calls exceed "
                    "the configured global tool-call cap."
                )
        return self


class DocumentRetrievalStatus(StrEnum):
    FETCHED = "fetched"
    NOT_FOUND = "not_found"
    NOT_ACCESSIBLE = "not_accessible"
    FAILED = "failed"


class DocumentParseStatus(StrEnum):
    PARSED = "parsed"
    PARTIAL = "partial"
    EMPTY = "empty"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    NOT_ATTEMPTED = "not_attempted"


class FieldExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    NOT_DISCLOSED = "not_disclosed"
    NOT_APPLICABLE = "not_applicable"
    NOT_FOUND = "not_found"
    NOT_ACCESSIBLE = "not_accessible"
    NOT_PROCESSED = "not_processed"


class ExtractionTaskStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NO_EVIDENCE = "no_evidence"
    NO_ACCESSIBLE_CONTENT = "no_accessible_content"
    CONTENT_ONLY = "content_only"
    NOT_PROCESSED = "not_processed"


class ExtractionConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ExtractionMethod(StrEnum):
    OPENAI = "openai"


class ExtractorClaimDraft(ClosedModel):
    """One model-proposed raw claim before deterministic grounding checks."""

    task_id: str
    target_field: str
    passage_id: str
    value_text: str = Field(min_length=1, max_length=2000)
    evidence_quote: str = Field(min_length=10, max_length=2000)
    asserted_by_text: str | None = Field(default=None, max_length=500)
    as_of_text: str | None = Field(default=None, max_length=500)
    unit_text: str | None = Field(default=None, max_length=200)
    currency_text: str | None = Field(default=None, max_length=100)
    publisher_text: str | None = Field(default=None, max_length=500)
    publication_date_text: str | None = Field(default=None, max_length=200)
    effective_date_text: str | None = Field(default=None, max_length=200)
    confidence: ExtractionConfidence
    notes: str = Field(default="", max_length=1000)


class ExtractorDraft(ClosedModel):
    """Structured provider output; lineage and evidence IDs remain local."""

    claims: list[ExtractorClaimDraft] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=20)


class SourceDocument(ClosedModel):
    document_id: str = Field(pattern=r"^document-[a-f0-9]{16}$")
    source_id: str = Field(pattern=r"^source-[a-f0-9]{16}$")
    canonical_url: str = Field(min_length=8, max_length=4000)
    final_url: str | None = Field(default=None, max_length=4000)
    redirect_chain: list[str] = Field(default_factory=list, max_length=10)
    task_ids: list[str] = Field(default_factory=list)
    retrieval_status: DocumentRetrievalStatus
    parse_status: DocumentParseStatus
    collected_at: datetime | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    media_type: str | None = Field(default=None, max_length=200)
    content_bytes: int | None = Field(default=None, ge=0)
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    content_path: str | None = Field(default=None, max_length=1000)
    title: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=250_000)
    text_chars: int = Field(default=0, ge=0)
    processed_chars: int = Field(default=0, ge=0)
    text_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    text_truncated: bool = False
    parser: str | None = Field(default=None, max_length=100)
    page_count: int | None = Field(default=None, ge=0)
    parsed_pages: int | None = Field(default=None, ge=0)
    selected_page_numbers: list[int] = Field(default_factory=list)
    resolution_method: str = Field(default="direct", max_length=100)
    resolver_metadata: dict[str, str] = Field(default_factory=dict)
    error_code: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    error_message: str | None = Field(default=None, max_length=500)

    @field_validator("content_path")
    @classmethod
    def validate_content_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if path.is_absolute() or not value or ".." in path.parts:
            raise ValueError("Document content_path must be a safe relative path.")
        return value

    @model_validator(mode="after")
    def validate_document(self) -> "SourceDocument":
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError("Source document task IDs must be unique.")
        if len(self.redirect_chain) != len(set(self.redirect_chain)):
            raise ValueError("Source document redirect chain cannot contain loops.")
        if len(self.resolver_metadata) > 30:
            raise ValueError("Source document resolver metadata is too large.")
        if len(self.selected_page_numbers) != len(set(self.selected_page_numbers)):
            raise ValueError("Selected PDF page numbers must be unique.")
        if self.selected_page_numbers != sorted(self.selected_page_numbers):
            raise ValueError("Selected PDF page numbers must be ordered.")
        if any(page < 1 for page in self.selected_page_numbers):
            raise ValueError("Selected PDF page numbers must be positive.")
        if self.text_chars != len(self.text):
            raise ValueError("Source document text_chars must match stored text.")
        if self.processed_chars > self.text_chars:
            raise ValueError("processed_chars cannot exceed text_chars.")
        parsed = self.parse_status in {
            DocumentParseStatus.PARSED,
            DocumentParseStatus.PARTIAL,
        }
        if parsed:
            if (
                self.retrieval_status != DocumentRetrievalStatus.FETCHED
                or not self.text
                or self.text_sha256 is None
                or self.parser is None
            ):
                raise ValueError(
                    "Parsed documents require fetched content, text, hash and parser."
                )
            expected_text_hash = hashlib.sha256(
                self.text.encode("utf-8")
            ).hexdigest()
            if self.text_sha256 != expected_text_hash:
                raise ValueError("Source document text SHA-256 does not match text.")
            if (self.parse_status == DocumentParseStatus.PARTIAL) != (
                self.text_truncated
            ):
                raise ValueError("Partial parse status must match text_truncated.")
        elif self.text or self.text_chars or self.text_sha256 is not None:
            raise ValueError("Unparsed documents cannot contain extracted text.")
        if self.retrieval_status == DocumentRetrievalStatus.FETCHED:
            if (
                self.final_url is None
                or self.collected_at is None
                or self.http_status is None
                or not 200 <= self.http_status < 300
                or self.media_type is None
                or self.content_bytes is None
                or self.content_sha256 is None
            ):
                raise ValueError(
                    "Fetched documents require URL, HTTP and content metadata."
                )
        elif parsed:
            raise ValueError("A non-fetched document cannot be parsed.")
        if self.parsed_pages is not None and self.page_count is not None:
            if self.parsed_pages > self.page_count:
                raise ValueError("parsed_pages cannot exceed page_count.")
        if self.page_count is not None and any(
            page > self.page_count for page in self.selected_page_numbers
        ):
            raise ValueError("Selected PDF page exceeds page_count.")
        if self.selected_page_numbers and self.media_type != "application/pdf":
            raise ValueError("Only PDF documents may record selected page numbers.")
        return self


class EvidencePassage(ClosedModel):
    passage_id: str = Field(pattern=r"^passage-[a-f0-9]{16}$")
    document_id: str = Field(pattern=r"^document-[a-f0-9]{16}$")
    source_id: str = Field(pattern=r"^source-[a-f0-9]{16}$")
    task_id: str
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    locator: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=10, max_length=6000)
    matched_terms: list[str] = Field(default_factory=list, max_length=50)


class ExtractionCitation(ClosedModel):
    citation_id: str = Field(pattern=r"^citation-[a-f0-9]{16}$")
    passage_id: str = Field(pattern=r"^passage-[a-f0-9]{16}$")
    document_id: str = Field(pattern=r"^document-[a-f0-9]{16}$")
    source_id: str = Field(pattern=r"^source-[a-f0-9]{16}$")
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    quote: str = Field(min_length=10, max_length=2000)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    locator: str = Field(min_length=1, max_length=500)


class RawExtractionClaim(ClosedModel):
    claim_id: str = Field(pattern=r"^claim-[a-f0-9]{16}$")
    task_id: str
    target_field: str
    value_text: str = Field(min_length=1, max_length=2000)
    citation_ids: list[str] = Field(min_length=1, max_length=20)
    asserted_by_text: str | None = Field(default=None, max_length=500)
    as_of_text: str | None = Field(default=None, max_length=500)
    unit_text: str | None = Field(default=None, max_length=200)
    currency_text: str | None = Field(default=None, max_length=100)
    publisher_text: str | None = Field(default=None, max_length=500)
    publication_date_text: str | None = Field(default=None, max_length=200)
    effective_date_text: str | None = Field(default=None, max_length=200)
    confidence: ExtractionConfidence
    verification_status: Literal["unverified"] = "unverified"
    extraction_method: ExtractionMethod = ExtractionMethod.OPENAI
    notes: str = Field(default="", max_length=1000)


class FieldExtractionResult(ClosedModel):
    task_id: str
    target_field: str
    status: FieldExtractionStatus
    claim_ids: list[str] = Field(default_factory=list)
    source_ids_considered: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=1000)


class ExtractionTaskResult(ClosedModel):
    task_id: str
    catalog_question_id: str
    status: ExtractionTaskStatus
    source_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    passage_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    field_results: list[FieldExtractionResult] = Field(min_length=1)
    unresolved_targets: list[str] = Field(default_factory=list)
    inherited_search_unresolved_targets: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=1000)


class ExtractionLimits(ClosedModel):
    source_limit: int | None = Field(default=None, ge=1)
    requested_source_ids: list[str] = Field(default_factory=list)
    max_document_bytes: int = Field(ge=1024)
    max_document_chars: int = Field(ge=1000, le=250_000)
    max_pdf_scan_chars: int = Field(default=2_000_000, ge=10_000, le=5_000_000)
    max_passages_per_task: int = Field(ge=1, le=50)
    max_evidence_chars_per_call: int = Field(
        default=100_000, ge=10_000, le=500_000
    )
    max_api_calls: int = Field(ge=1, le=100)


class ExtractionAttemptFailure(ClosedModel):
    call_index: int = Field(ge=1)
    source_id: str = Field(pattern=r"^source-[a-f0-9]{16}$")
    scope_task_ids: list[str] = Field(min_length=1)
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    usage_recorded: bool
    token_usage_unknown: bool = False


class ExtractionResults(ClosedModel):
    """Auditable raw extraction artifact consumed later by Checker."""

    schema_version: Literal["1.0.0"] = EXTRACTOR_SCHEMA_VERSION
    prompt_version: str = EXTRACTOR_PROMPT_VERSION
    extraction_id: str
    plan_run_id: str
    search_id: str
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    search_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_reference: str = Field(min_length=1)
    search_reference: str = Field(min_length=1)
    created_at: datetime
    iteration: int = Field(ge=1)
    generated_by: Literal["deterministic", "openai"]
    model: str | None
    brand_name: str
    target_country: str = Field(pattern=r"^[A-Z]{2}$")
    depth: ResearchDepth
    network_executed: bool
    provider_executed: bool
    limits: ExtractionLimits
    selected_task_ids: list[str] = Field(min_length=1)
    selected_source_ids: list[str] = Field(min_length=1)
    unselected_source_ids: list[str]
    documents: list[SourceDocument] = Field(min_length=1)
    evidence_passages: list[EvidencePassage]
    citations: list[ExtractionCitation]
    claims: list[RawExtractionClaim]
    task_results: list[ExtractionTaskResult] = Field(min_length=1)
    warnings: list[str]
    compliance_rules: list[str]
    agent_usage: list[AgentIterationUsage] = Field(default_factory=list)
    failed_attempts: list[ExtractionAttemptFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_extraction_results(self) -> "ExtractionResults":
        for value, field_name in (
            (self.extraction_id, "extraction_id"),
            (self.plan_run_id, "plan_run_id"),
            (self.search_id, "search_id"),
        ):
            try:
                parsed = UUID(value)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"{field_name} must be a valid UUIDv4.") from exc
            if parsed.version != 4:
                raise ValueError(f"{field_name} must be a valid UUIDv4.")
        if self.generated_by == "deterministic" and (
            self.model is not None
            or self.provider_executed
            or self.agent_usage
            or self.failed_attempts
            or self.claims
            or self.citations
        ):
            raise ValueError(
                "Deterministic Extractor cannot contain provider facts or claims."
            )
        for values, field_name in (
            (self.selected_task_ids, "selected_task_ids"),
            (self.selected_source_ids, "selected_source_ids"),
            (self.unselected_source_ids, "unselected_source_ids"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} values must be unique.")
        if set(self.selected_source_ids) & set(self.unselected_source_ids):
            raise ValueError("Selected and unselected extraction sources overlap.")

        document_ids = [item.document_id for item in self.documents]
        document_source_ids = [item.source_id for item in self.documents]
        if document_source_ids != self.selected_source_ids:
            raise ValueError(
                "Extraction documents must exactly follow selected_source_ids."
            )
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("Extraction document IDs must be unique.")
        document_by_id = {item.document_id: item for item in self.documents}
        document_by_source = {item.source_id: item for item in self.documents}
        known_tasks = set(self.selected_task_ids)
        if any(not set(item.task_ids).issubset(known_tasks) for item in self.documents):
            raise ValueError("Document task mappings exceed selected tasks.")

        passage_ids = [item.passage_id for item in self.evidence_passages]
        if len(passage_ids) != len(set(passage_ids)):
            raise ValueError("Evidence passage IDs must be unique.")
        passage_by_id = {item.passage_id: item for item in self.evidence_passages}
        for passage in self.evidence_passages:
            document = document_by_id.get(passage.document_id)
            if (
                document is None
                or document.source_id != passage.source_id
                or passage.task_id not in document.task_ids
                or document.parse_status
                not in {DocumentParseStatus.PARSED, DocumentParseStatus.PARTIAL}
                or passage.end_char <= passage.start_char
                or document.text[passage.start_char : passage.end_char]
                != passage.text
            ):
                raise ValueError("Evidence passage is not grounded in its document.")
            if len(passage.matched_terms) != len(set(passage.matched_terms)):
                raise ValueError("Evidence passage matched terms must be unique.")

        citation_ids = [item.citation_id for item in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("Extraction citation IDs must be unique.")
        citation_by_id = {item.citation_id: item for item in self.citations}
        for citation in self.citations:
            document = document_by_id.get(citation.document_id)
            passage = passage_by_id.get(citation.passage_id)
            if (
                document is None
                or passage is None
                or passage.document_id != citation.document_id
                or passage.source_id != citation.source_id
                or document.source_id != citation.source_id
                or document.text_sha256 != citation.text_sha256
                or citation.end_char <= citation.start_char
                or citation.start_char < passage.start_char
                or citation.end_char > passage.end_char
                or document.text[citation.start_char : citation.end_char]
                != citation.quote
            ):
                raise ValueError("Extraction citation is not grounded in source text.")

        claim_ids = [item.claim_id for item in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Raw extraction claim IDs must be unique.")
        claim_by_id = {item.claim_id: item for item in self.claims}
        for claim in self.claims:
            if claim.task_id not in known_tasks:
                raise ValueError("Raw extraction claim references unknown task.")
            if len(claim.citation_ids) != len(set(claim.citation_ids)):
                raise ValueError("Raw claim citation IDs must be unique.")
            if not set(claim.citation_ids).issubset(citation_by_id):
                raise ValueError("Raw extraction claim references unknown citations.")
            claim_citations = [citation_by_id[item] for item in claim.citation_ids]
            if any(
                claim.task_id
                not in document_by_source[citation.source_id].task_ids
                for citation in claim_citations
            ):
                raise ValueError("Raw claim citation source is not mapped to task.")
            if any(
                passage_by_id[citation.passage_id].task_id != claim.task_id
                for citation in claim_citations
            ):
                raise ValueError("Raw claim citation passage is mapped to another task.")
            if not any(
                claim.value_text in citation.quote for citation in claim_citations
            ):
                raise ValueError("Raw claim value must occur in a citation quote.")

        if [item.task_id for item in self.task_results] != self.selected_task_ids:
            raise ValueError(
                "Extraction task_results must follow selected_task_ids exactly."
            )
        result_by_task = {item.task_id: item for item in self.task_results}
        for result in self.task_results:
            for values, field_name in (
                (result.source_ids, "source_ids"),
                (result.document_ids, "document_ids"),
                (result.passage_ids, "passage_ids"),
                (result.claim_ids, "claim_ids"),
                (result.unresolved_targets, "unresolved_targets"),
                (
                    result.inherited_search_unresolved_targets,
                    "inherited_search_unresolved_targets",
                ),
                (result.coverage_gaps, "coverage_gaps"),
            ):
                if len(values) != len(set(values)):
                    raise ValueError(
                        f"Extraction task {field_name} values must be unique."
                    )
            if not set(result.source_ids).issubset(self.selected_source_ids):
                raise ValueError("Extraction task references unknown source IDs.")
            if not set(result.document_ids).issubset(document_by_id):
                raise ValueError("Extraction task references unknown document IDs.")
            if not set(result.passage_ids).issubset(passage_by_id):
                raise ValueError("Extraction task references unknown passage IDs.")
            if not set(result.claim_ids).issubset(claim_by_id):
                raise ValueError("Extraction task references unknown claim IDs.")
            expected_documents = [
                document
                for document in self.documents
                if result.task_id in document.task_ids
            ]
            expected_source_ids = [item.source_id for item in expected_documents]
            expected_document_ids = [item.document_id for item in expected_documents]
            expected_passage_ids = [
                item.passage_id
                for item in self.evidence_passages
                if item.task_id == result.task_id
            ]
            expected_claim_ids = [
                item.claim_id
                for item in self.claims
                if item.task_id == result.task_id
            ]
            if (
                result.source_ids != expected_source_ids
                or result.document_ids != expected_document_ids
                or result.passage_ids != expected_passage_ids
                or result.claim_ids != expected_claim_ids
            ):
                raise ValueError(
                    "Extraction task source, document, passage and claim mappings "
                    "must be exact and symmetric."
                )
            if any(
                document_by_id[item].source_id not in result.source_ids
                for item in result.document_ids
            ):
                raise ValueError("Task documents must belong to its source set.")
            if any(
                passage_by_id[item].task_id != result.task_id
                for item in result.passage_ids
            ):
                raise ValueError("Task passage mapping is not symmetric.")
            if any(claim_by_id[item].task_id != result.task_id for item in result.claim_ids):
                raise ValueError("Task claim mapping is not symmetric.")
            field_names = [item.target_field for item in result.field_results]
            if len(field_names) != len(set(field_names)):
                raise ValueError("Task field extraction results must be unique.")
            for field_result in result.field_results:
                if field_result.task_id != result.task_id:
                    raise ValueError("Field extraction result has wrong task ID.")
                if len(field_result.claim_ids) != len(set(field_result.claim_ids)):
                    raise ValueError("Field result claim IDs must be unique.")
                if len(field_result.source_ids_considered) != len(
                    set(field_result.source_ids_considered)
                ):
                    raise ValueError("Field result source IDs must be unique.")
                if not set(field_result.claim_ids).issubset(claim_by_id):
                    raise ValueError("Field result references unknown raw claims.")
                if not set(field_result.source_ids_considered).issubset(
                    result.source_ids
                ):
                    raise ValueError("Field result references unconsidered task sources.")
                if field_result.source_ids_considered != result.source_ids:
                    raise ValueError(
                        "Field result must record every selected source mapped to task."
                    )
                field_claims = [claim_by_id[item] for item in field_result.claim_ids]
                if any(
                    claim.target_field != field_result.target_field
                    for claim in field_claims
                ):
                    raise ValueError("Field result claim mappings are inconsistent.")
                expected_field_claim_ids = [
                    claim.claim_id
                    for claim in self.claims
                    if claim.task_id == result.task_id
                    and claim.target_field == field_result.target_field
                ]
                if field_result.claim_ids != expected_field_claim_ids:
                    raise ValueError(
                        "Field result must exactly contain its global raw claims."
                    )
                if field_result.status in {
                    FieldExtractionStatus.EXTRACTED,
                    FieldExtractionStatus.NOT_DISCLOSED,
                    FieldExtractionStatus.NOT_APPLICABLE,
                } and not field_result.claim_ids:
                    raise ValueError("Extracted field status requires raw claims.")
                if field_result.status in {
                    FieldExtractionStatus.NOT_FOUND,
                    FieldExtractionStatus.NOT_ACCESSIBLE,
                    FieldExtractionStatus.NOT_PROCESSED,
                } and field_result.claim_ids:
                    raise ValueError("Non-extracted field status cannot have claims.")
            closed_field_statuses = {
                FieldExtractionStatus.EXTRACTED,
                FieldExtractionStatus.NOT_DISCLOSED,
                FieldExtractionStatus.NOT_APPLICABLE,
            }
            closed_count = sum(
                field.status in closed_field_statuses
                for field in result.field_results
            )
            if result.status == ExtractionTaskStatus.COMPLETE and closed_count != len(
                result.field_results
            ):
                raise ValueError("Complete extraction task requires all fields closed.")
            if result.status == ExtractionTaskStatus.PARTIAL and not (
                0 < closed_count < len(result.field_results)
            ):
                raise ValueError(
                    "Partial extraction task requires both closed and open fields."
                )

        for claim in self.claims:
            target_fields = {
                item.target_field
                for item in result_by_task[claim.task_id].field_results
            }
            if claim.target_field not in target_fields:
                raise ValueError("Raw extraction claim targets an unknown plan field.")

        usage_keys = [
            (item.agent, item.iteration, item.call_index)
            for item in self.agent_usage
        ]
        if len(usage_keys) != len(set(usage_keys)):
            raise ValueError("Extractor usage entries must be unique.")
        if any(
            item.agent != "extractor"
            or item.iteration != self.iteration
            or not set(item.scope_task_ids).issubset(known_tasks)
            or len(item.scope_source_ids) != 1
            or item.scope_source_ids[0] not in self.selected_source_ids
            for item in self.agent_usage
        ):
            raise ValueError("Extractor usage has inconsistent agent scope.")
        usage_by_call_index = {
            item.call_index: item for item in self.agent_usage
        }
        usage_call_indices = {item.call_index for item in self.agent_usage}
        if len(self.failed_attempts) != len(
            {item.call_index for item in self.failed_attempts}
        ):
            raise ValueError("Extractor failed call indices must be unique.")
        for failure in self.failed_attempts:
            if (
                failure.source_id not in self.selected_source_ids
                or not set(failure.scope_task_ids).issubset(known_tasks)
                or failure.usage_recorded
                != (failure.call_index in usage_call_indices)
                or failure.token_usage_unknown == failure.usage_recorded
            ):
                raise ValueError("Extractor failure ledger is inconsistent.")
            if failure.usage_recorded and usage_by_call_index[
                failure.call_index
            ].scope_source_ids != [failure.source_id]:
                raise ValueError("Extractor failure usage has wrong source scope.")

        logical_call_indices = usage_call_indices | {
            item.call_index for item in self.failed_attempts
        }
        if len(logical_call_indices) > self.limits.max_api_calls:
            raise ValueError("Extractor logical calls exceed max_api_calls.")
        if logical_call_indices and sorted(logical_call_indices) != list(
            range(1, len(logical_call_indices) + 1)
        ):
            raise ValueError("Extractor logical call indices must be contiguous.")

        failed_call_indices = {
            item.call_index for item in self.failed_attempts
        }
        successful_usage = [
            usage
            for usage in self.agent_usage
            if usage.call_index not in failed_call_indices
        ]
        closed_field_statuses = {
            FieldExtractionStatus.EXTRACTED,
            FieldExtractionStatus.NOT_DISCLOSED,
            FieldExtractionStatus.NOT_APPLICABLE,
        }
        for result in self.task_results:
            accessible = any(
                document_by_id[document_id].parse_status
                in {DocumentParseStatus.PARSED, DocumentParseStatus.PARTIAL}
                for document_id in result.document_ids
            )
            closed_count = sum(
                field.status in closed_field_statuses
                for field in result.field_results
            )
            semantic_success = any(
                result.task_id in usage.scope_task_ids
                and usage.scope_source_ids[0] in result.source_ids
                for usage in successful_usage
            )
            if not accessible:
                expected_status = ExtractionTaskStatus.NO_ACCESSIBLE_CONTENT
            elif self.generated_by == "deterministic":
                expected_status = ExtractionTaskStatus.CONTENT_ONLY
            elif closed_count == len(result.field_results):
                expected_status = ExtractionTaskStatus.COMPLETE
            elif closed_count:
                expected_status = ExtractionTaskStatus.PARTIAL
            elif semantic_success:
                expected_status = ExtractionTaskStatus.NO_EVIDENCE
            else:
                expected_status = ExtractionTaskStatus.NOT_PROCESSED
            if result.status != expected_status:
                raise ValueError(
                    "Extraction task status does not match deterministic evidence "
                    "and provider-attempt state."
                )

        if self.generated_by == "openai":
            if self.model is None or not self.model.strip():
                raise ValueError("OpenAI Extractor must declare its model.")
            if self.provider_executed != bool(
                self.agent_usage or self.failed_attempts
            ):
                raise ValueError(
                    "Extractor provider_executed must match recorded provider attempts."
                )
        return self


class CheckerModelVerdict(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"


class CheckerVerdict(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    NOT_REVIEWED = "not_reviewed"


class CheckerModelSemanticFit(StrEnum):
    DIRECT = "direct"
    PARTIAL = "partial"
    MISMATCH = "mismatch"


class CheckerSemanticFit(StrEnum):
    DIRECT = "direct"
    PARTIAL = "partial"
    MISMATCH = "mismatch"
    NOT_REVIEWED = "not_reviewed"


class CheckerModelSourceSupport(StrEnum):
    SUFFICIENT = "sufficient"
    NEEDS_CORROBORATION = "needs_corroboration"
    UNSUITABLE = "unsuitable"


class CheckerSourceSupport(StrEnum):
    SUFFICIENT = "sufficient"
    NEEDS_CORROBORATION = "needs_corroboration"
    UNSUITABLE = "unsuitable"
    NOT_REVIEWED = "not_reviewed"


class CheckerIssueCode(StrEnum):
    AMBIGUOUS_SCOPE = "ambiguous_scope"
    CATEGORY_NOT_ITEM = "category_not_item"
    CONFLICTING_VALUES = "conflicting_values"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    INSUFFICIENT_SOURCES = "insufficient_sources"
    INACCESSIBLE_SOURCE = "inaccessible_source"
    KNOWN_UNSELECTED_SOURCE = "known_unselected_source"
    MENTIONED_NOT_OBTAINED = "mentioned_not_obtained"
    NEEDS_INDEPENDENT_CORROBORATION = "needs_independent_corroboration"
    OPINION_NOT_LABELED = "opinion_not_labeled"
    PERSONAL_DATA = "personal_data"
    PREFERRED_SOURCE_MISSING = "preferred_source_missing"
    SELF_DECLARATION_ONLY = "self_declaration_only"
    SOURCE_ROLE_MISMATCH = "source_role_mismatch"
    STALE_OR_UNDATED = "stale_or_undated"
    UNPROCESSED_FIELD = "unprocessed_field"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    UNSUPPORTED_FIELD_MAPPING = "unsupported_field_mapping"


class CheckerContradictionKind(StrEnum):
    CONFLICTING_VALUES = "conflicting_values"
    SCOPE_MISMATCH = "scope_mismatch"
    TEMPORAL_MISMATCH = "temporal_mismatch"


class CheckerUnsafeCategory(StrEnum):
    EXCESS_PERSONAL_DATA = "excess_personal_data"
    OPINION_AS_FACT = "opinion_as_fact"
    PROHIBITED_SOURCE_USE = "prohibited_source_use"
    SENSITIVE_UNCORROBORATED = "sensitive_uncorroborated"


class CheckerSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SourceAuthorityClass(StrEnum):
    PRIMARY_AUTHORITY = "primary_authority"
    PRIMARY_SELF_REPORT = "primary_self_report"
    INDEPENDENT_SECONDARY = "independent_secondary"
    OPINION_OR_LEAD = "opinion_or_lead"
    ROUTING_ONLY = "routing_only"
    UNKNOWN = "unknown"


class SourceIndependence(StrEnum):
    FIRST_PARTY = "first_party"
    INDEPENDENT = "independent"
    MIXED_OR_UNKNOWN = "mixed_or_unknown"


class CheckerFieldStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    NEEDS_CORROBORATION = "needs_corroboration"
    NEEDS_REVIEW = "needs_review"
    CONFLICTING = "conflicting"
    REJECTED = "rejected"
    MISSING = "missing"
    NOT_ACCESSIBLE = "not_accessible"
    NOT_REVIEWED = "not_reviewed"


class CheckerTaskStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    CONFLICTING = "conflicting"
    MISSING = "missing"
    NOT_ACCESSIBLE = "not_accessible"
    NOT_REVIEWED = "not_reviewed"


class CheckerFollowUpReason(StrEnum):
    MISSING_CLAIM = "missing_claim"
    REJECTED_CLAIM = "rejected_claim"
    COMPLETE_PARTIAL_FIELD = "complete_partial_field"
    NEEDS_SEMANTIC_REVIEW = "needs_semantic_review"
    NEEDS_CORROBORATION = "needs_corroboration"
    RESOLVE_CONTRADICTION = "resolve_contradiction"
    SOURCE_NOT_ACCESSIBLE = "source_not_accessible"


class CheckerFollowUpRoute(StrEnum):
    RESOLVER = "resolver"
    HUMAN_REVIEW = "human_review"


class CheckerFollowUpAction(StrEnum):
    EXTRACT_KNOWN_SOURCE = "extract_known_source"
    REEXTRACT_EXISTING = "reextract_existing"
    RETRY_RETRIEVAL = "retry_retrieval"
    FIND_ALTERNATIVE_SOURCE = "find_alternative_source"
    CORROBORATE = "corroborate"
    RESOLVE_CONFLICT = "resolve_conflict"
    SEMANTIC_REVIEW = "semantic_review"


class CheckerNextAction(StrEnum):
    RUN_PAID_CHECKER = "run_paid_checker"
    RETRY_CHECKER = "retry_checker"
    RESOLVE_GAPS = "resolve_gaps"
    HUMAN_REVIEW = "human_review"


class CheckerClaimDecisionDraft(ClosedModel):
    claim_id: str = Field(pattern=r"^claim-[a-f0-9]{16}$")
    verdict: CheckerModelVerdict
    semantic_fit: CheckerModelSemanticFit
    source_support: CheckerModelSourceSupport
    issue_codes: list[CheckerIssueCode] = Field(default_factory=list, max_length=12)
    rationale: str = Field(min_length=5, max_length=1000)

    @model_validator(mode="after")
    def validate_decision(self) -> "CheckerClaimDecisionDraft":
        if len(self.issue_codes) != len(set(self.issue_codes)):
            raise ValueError("Checker decision issue codes must be unique.")
        if self.verdict == CheckerModelVerdict.ACCEPTED and (
            self.semantic_fit == CheckerModelSemanticFit.MISMATCH
            or self.source_support == CheckerModelSourceSupport.UNSUITABLE
        ):
            raise ValueError(
                "An accepted Checker decision cannot be a semantic mismatch or "
                "use unsuitable source support."
            )
        if self.verdict in {
            CheckerModelVerdict.REJECTED,
            CheckerModelVerdict.NEEDS_REVIEW,
        } and not self.issue_codes:
            raise ValueError(
                "Rejected or needs-review Checker decisions require an issue code."
            )
        return self


class CheckerContradictionDraft(ClosedModel):
    target_field: str = Field(min_length=1, max_length=500)
    claim_ids: list[str] = Field(min_length=2, max_length=20)
    kind: CheckerContradictionKind
    rationale: str = Field(min_length=5, max_length=1000)


class CheckerUnsafeItemDraft(ClosedModel):
    category: CheckerUnsafeCategory
    severity: CheckerSeverity
    claim_ids: list[str] = Field(default_factory=list, max_length=20)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=5, max_length=1000)


class CheckerDraft(ClosedModel):
    """Provider judgment before local completeness, lineage, and scoring checks."""

    decisions: list[CheckerClaimDecisionDraft] = Field(
        default_factory=list, max_length=500
    )
    contradictions: list[CheckerContradictionDraft] = Field(
        default_factory=list, max_length=100
    )
    unsafe_items: list[CheckerUnsafeItemDraft] = Field(
        default_factory=list, max_length=100
    )
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_draft(self) -> "CheckerDraft":
        claim_ids = [item.claim_id for item in self.decisions]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("Checker draft decisions must have unique claim IDs.")
        for contradiction in self.contradictions:
            if len(contradiction.claim_ids) != len(set(contradiction.claim_ids)):
                raise ValueError("Checker contradiction claim IDs must be unique.")
        return self


class CheckerSourceAssessment(ClosedModel):
    source_id: str = Field(pattern=r"^source-[a-f0-9]{16}$")
    document_id: str = Field(pattern=r"^document-[a-f0-9]{16}$")
    source_type: SourceType
    publisher_key: str = Field(min_length=1, max_length=500)
    retrieval_status: DocumentRetrievalStatus
    parse_status: DocumentParseStatus
    authority_class: SourceAuthorityClass
    independence: SourceIndependence
    reliability_score: int = Field(ge=0, le=100)
    caveats: list[str] = Field(default_factory=list, max_length=10)


class CheckerClaimDecision(ClosedModel):
    claim_id: str = Field(pattern=r"^claim-[a-f0-9]{16}$")
    task_id: str
    target_field: str
    source_ids: list[str] = Field(min_length=1, max_length=20)
    grounding_verified: Literal[True] = True
    verdict: CheckerVerdict
    semantic_fit: CheckerSemanticFit
    source_support: CheckerSourceSupport
    issue_codes: list[CheckerIssueCode] = Field(default_factory=list, max_length=12)
    rationale: str = Field(default="", max_length=1000)


class CheckerContradiction(ClosedModel):
    contradiction_id: str = Field(pattern=r"^contradiction-[a-f0-9]{16}$")
    task_id: str
    target_field: str
    claim_ids: list[str] = Field(min_length=2, max_length=20)
    kind: CheckerContradictionKind
    rationale: str = Field(min_length=5, max_length=1000)
    resolved: Literal[False] = False


class CheckerUnsafeItem(ClosedModel):
    unsafe_item_id: str = Field(pattern=r"^unsafe-[a-f0-9]{16}$")
    category: CheckerUnsafeCategory
    severity: CheckerSeverity
    claim_ids: list[str] = Field(default_factory=list, max_length=20)
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(min_length=5, max_length=1000)


class CheckerFieldResult(ClosedModel):
    task_id: str
    target_field: str
    status: CheckerFieldStatus
    raw_claim_ids: list[str] = Field(default_factory=list)
    accepted_claim_ids: list[str] = Field(default_factory=list)
    rejected_claim_ids: list[str] = Field(default_factory=list)
    needs_review_claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    issue_codes: list[CheckerIssueCode] = Field(default_factory=list)
    quality_points: Decimal = Field(ge=0, le=1)


class CheckerFollowUpTask(ClosedModel):
    follow_up_id: str = Field(pattern=r"^followup-[a-f0-9]{16}$")
    task_id: str
    target_field: str
    priority: Priority
    reason: CheckerFollowUpReason
    question: str = Field(min_length=10, max_length=2000)
    required_source_types: list[SourceType] = Field(default_factory=list, max_length=20)
    related_claim_ids: list[str] = Field(default_factory=list, max_length=20)
    supporting_claim_ids: list[str] = Field(default_factory=list, max_length=20)
    route: CheckerFollowUpRoute = CheckerFollowUpRoute.RESOLVER
    action: CheckerFollowUpAction = CheckerFollowUpAction.FIND_ALTERNATIVE_SOURCE
    candidate_source_ids: list[str] = Field(default_factory=list, max_length=100)
    retry_source_ids: list[str] = Field(default_factory=list, max_length=100)
    reextract_source_ids: list[str] = Field(default_factory=list, max_length=100)
    minimum_additional_sources: int = Field(default=1, ge=0, le=20)
    requires_independent_source: bool = False
    suggested_queries: list[str] = Field(default_factory=list, max_length=10)
    completion_criteria: str = Field(
        default="Resolve the field with grounded evidence.",
        min_length=10,
        max_length=2000,
    )
    status: Literal["pending"] = "pending"


class CheckerTaskResult(ClosedModel):
    task_id: str
    catalog_question_id: str
    priority: Priority
    requirement: Requirement
    status: CheckerTaskStatus
    field_results: list[CheckerFieldResult] = Field(min_length=1)
    follow_up_ids: list[str] = Field(default_factory=list)


class CheckerScoreBreakdown(ClosedModel):
    scoring_version: Literal["checker-scoring-v1", "checker-scoring-v2"] = (
        CHECKER_SCORING_VERSION
    )
    raw_coverage_score: int = Field(ge=0, le=100)
    verified_coverage_score: int = Field(ge=0, le=100)
    semantic_acceptance_score: int | None = Field(default=None, ge=0, le=100)
    accepted_claim_source_quality_score: int | None = Field(
        default=None,
        ge=0,
        le=100,
        validation_alias=AliasChoices(
            "accepted_claim_source_quality_score", "source_quality_score"
        ),
    )
    whole_plan_coverage_score: int = Field(ge=0, le=100)
    deduction_points: int = Field(default=0, ge=0, le=100)
    quality_score: int = Field(ge=0, le=100)


class CheckerLimits(ClosedModel):
    max_claims: int = Field(ge=1, le=500)
    max_evidence_chars: int = Field(ge=1_000, le=500_000)
    max_api_calls: Literal[1] = 1


class CheckerAttemptFailure(ClosedModel):
    call_index: Literal[1] = 1
    scope_task_ids: list[str] = Field(min_length=1)
    scope_source_ids: list[str] = Field(default_factory=list)
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    usage_recorded: bool
    token_usage_unknown: bool = False


class CheckerResults(ClosedModel):
    """Auditable quality decision consumed by Resolver and human review."""

    schema_version: Literal["1.0.0", "1.1.0"] = CHECKER_SCHEMA_VERSION
    prompt_version: Literal["checker-system-v1", "checker-system-v2"] = (
        CHECKER_PROMPT_VERSION
    )
    check_id: str
    plan_run_id: str
    search_id: str
    extraction_id: str
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    search_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extraction_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_reference: str = Field(min_length=1)
    search_reference: str = Field(min_length=1)
    extraction_reference: str = Field(min_length=1)
    created_at: datetime
    iteration: int = Field(ge=1)
    generated_by: Literal["deterministic", "openai"]
    model: str | None
    brand_name: str
    target_country: str = Field(pattern=r"^[A-Z]{2}$")
    depth: ResearchDepth
    provider_executed: bool
    quality_threshold: int = Field(ge=0, le=100)
    limits: CheckerLimits
    selected_task_ids: list[str] = Field(min_length=1)
    selected_source_ids: list[str] = Field(min_length=1)
    selected_claim_ids: list[str]
    unevaluated_task_ids: list[str]
    unevaluated_source_ids: list[str]
    scope_complete: bool
    source_assessments: list[CheckerSourceAssessment]
    claim_decisions: list[CheckerClaimDecision]
    contradictions: list[CheckerContradiction]
    unsafe_items: list[CheckerUnsafeItem]
    task_results: list[CheckerTaskResult] = Field(min_length=1)
    critical_missing_fields: list[str]
    unevaluated_critical_fields: list[str]
    follow_up_tasks: list[CheckerFollowUpTask]
    score_breakdown: CheckerScoreBreakdown
    quality_score: int = Field(ge=0, le=100)
    passed: bool
    recommended_next_action: CheckerNextAction
    warnings: list[str]
    compliance_rules: list[str]
    agent_usage: list[AgentIterationUsage] = Field(default_factory=list)
    failed_attempts: list[CheckerAttemptFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_checker_results(self) -> "CheckerResults":
        for value, field_name in (
            (self.check_id, "check_id"),
            (self.plan_run_id, "plan_run_id"),
            (self.search_id, "search_id"),
            (self.extraction_id, "extraction_id"),
        ):
            try:
                parsed = UUID(value)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"{field_name} must be a valid UUIDv4.") from exc
            if parsed.version != 4:
                raise ValueError(f"{field_name} must be a valid UUIDv4.")

        for values, field_name in (
            (self.selected_task_ids, "selected_task_ids"),
            (self.selected_source_ids, "selected_source_ids"),
            (self.selected_claim_ids, "selected_claim_ids"),
            (self.unevaluated_task_ids, "unevaluated_task_ids"),
            (self.unevaluated_source_ids, "unevaluated_source_ids"),
            (self.critical_missing_fields, "critical_missing_fields"),
            (self.unevaluated_critical_fields, "unevaluated_critical_fields"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Checker {field_name} values must be unique.")

        if [item.source_id for item in self.source_assessments] != (
            self.selected_source_ids
        ):
            raise ValueError("Checker source assessments must follow source order.")
        if any(
            len(item.caveats) != len(set(item.caveats))
            for item in self.source_assessments
        ):
            raise ValueError("Checker source assessment caveats must be unique.")
        document_ids = [item.document_id for item in self.source_assessments]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("Checker source assessment document IDs must be unique.")
        if set(self.selected_task_ids) & set(self.unevaluated_task_ids):
            raise ValueError("Selected and unevaluated Checker tasks overlap.")
        if set(self.selected_source_ids) & set(self.unevaluated_source_ids):
            raise ValueError("Selected and unevaluated Checker sources overlap.")
        expected_scope_complete = not (
            self.unevaluated_task_ids or self.unevaluated_source_ids
        )
        if self.scope_complete != expected_scope_complete:
            raise ValueError("Checker scope_complete is inconsistent with its scope.")
        if [item.claim_id for item in self.claim_decisions] != self.selected_claim_ids:
            raise ValueError("Checker decisions must cover claims in exact order.")
        if len(self.selected_claim_ids) > self.limits.max_claims:
            raise ValueError("Checker selected claims exceed max_claims.")

        decision_by_id = {item.claim_id: item for item in self.claim_decisions}
        known_tasks = set(self.selected_task_ids)
        known_sources = set(self.selected_source_ids)
        known_all_sources = known_sources | set(self.unevaluated_source_ids)
        if any(
            item.task_id not in known_tasks
            or not set(item.source_ids).issubset(known_sources)
            or len(item.source_ids) != len(set(item.source_ids))
            or len(item.issue_codes) != len(set(item.issue_codes))
            for item in self.claim_decisions
        ):
            raise ValueError("Checker claim decision scope is inconsistent.")

        if self.generated_by == "deterministic":
            if (
                self.model is not None
                or self.provider_executed
                or self.agent_usage
                or self.failed_attempts
                or self.contradictions
                or self.unsafe_items
            ):
                raise ValueError(
                    "Deterministic Checker cannot contain provider judgments."
                )
            if any(
                item.verdict != CheckerVerdict.NOT_REVIEWED
                or item.semantic_fit != CheckerSemanticFit.NOT_REVIEWED
                or item.source_support != CheckerSourceSupport.NOT_REVIEWED
                for item in self.claim_decisions
            ):
                raise ValueError(
                    "Deterministic Checker decisions must remain not_reviewed."
                )
        else:
            if self.model is None or not self.model.strip():
                raise ValueError("OpenAI Checker must declare its model.")
            if self.provider_executed != bool(
                self.agent_usage or self.failed_attempts
            ):
                raise ValueError(
                    "Checker provider_executed must match recorded attempts."
                )
            if not self.failed_attempts and any(
                item.verdict == CheckerVerdict.NOT_REVIEWED
                for item in self.claim_decisions
            ):
                raise ValueError(
                    "Successful paid Checker cannot leave claims unreviewed."
                )
            if self.failed_attempts and any(
                item.verdict != CheckerVerdict.NOT_REVIEWED
                or item.semantic_fit != CheckerSemanticFit.NOT_REVIEWED
                or item.source_support != CheckerSourceSupport.NOT_REVIEWED
                for item in self.claim_decisions
            ):
                raise ValueError(
                    "A failed single-call Checker cannot retain partial judgments."
                )
            if (
                self.selected_claim_ids
                and not self.failed_attempts
                and len(self.agent_usage) != 1
            ):
                raise ValueError(
                    "Successful paid Checker claims require exactly one usage entry."
                )

        contradiction_ids = [item.contradiction_id for item in self.contradictions]
        if len(contradiction_ids) != len(set(contradiction_ids)):
            raise ValueError("Checker contradiction IDs must be unique.")
        for item in self.contradictions:
            if (
                len(item.claim_ids) != len(set(item.claim_ids))
                or not set(item.claim_ids).issubset(decision_by_id)
            ):
                raise ValueError("Checker contradiction claim scope is invalid.")
            decisions = [decision_by_id[claim_id] for claim_id in item.claim_ids]
            if any(
                decision.task_id != item.task_id
                or decision.target_field != item.target_field
                for decision in decisions
            ):
                raise ValueError(
                    "Checker contradiction claims must share task and field."
                )

        unsafe_ids = [item.unsafe_item_id for item in self.unsafe_items]
        if len(unsafe_ids) != len(set(unsafe_ids)):
            raise ValueError("Checker unsafe item IDs must be unique.")
        if any(
            not set(item.claim_ids).issubset(decision_by_id)
            or not set(item.source_ids).issubset(known_sources)
            or (not item.claim_ids and not item.source_ids)
            or len(item.claim_ids) != len(set(item.claim_ids))
            or len(item.source_ids) != len(set(item.source_ids))
            for item in self.unsafe_items
        ):
            raise ValueError("Checker unsafe item scope is invalid.")

        if [item.task_id for item in self.task_results] != self.selected_task_ids:
            raise ValueError("Checker task results must follow task order.")
        field_result_by_key: dict[tuple[str, str], CheckerFieldResult] = {}
        for task_result in self.task_results:
            fields = [item.target_field for item in task_result.field_results]
            if len(fields) != len(set(fields)):
                raise ValueError("Checker task field results must be unique.")
            for field_result in task_result.field_results:
                if field_result.task_id != task_result.task_id:
                    raise ValueError("Checker field result has wrong task ID.")
                key = (field_result.task_id, field_result.target_field)
                field_result_by_key[key] = field_result
                for values in (
                    field_result.raw_claim_ids,
                    field_result.accepted_claim_ids,
                    field_result.rejected_claim_ids,
                    field_result.needs_review_claim_ids,
                    field_result.source_ids,
                    field_result.issue_codes,
                ):
                    if len(values) != len(set(values)):
                        raise ValueError("Checker field result lists must be unique.")
                if not set(field_result.raw_claim_ids).issubset(decision_by_id):
                    raise ValueError("Checker field references unknown raw claims.")
                if not set(field_result.source_ids).issubset(known_sources):
                    raise ValueError("Checker field references unknown sources.")
                expected = [
                    decision.claim_id
                    for decision in self.claim_decisions
                    if decision.task_id == field_result.task_id
                    and decision.target_field == field_result.target_field
                ]
                if field_result.raw_claim_ids != expected:
                    raise ValueError(
                        "Checker field raw claims must match global decisions."
                    )
                expected_accepted = [
                    claim_id
                    for claim_id in expected
                    if decision_by_id[claim_id].verdict
                    == CheckerVerdict.ACCEPTED
                ]
                expected_rejected = [
                    claim_id
                    for claim_id in expected
                    if decision_by_id[claim_id].verdict
                    == CheckerVerdict.REJECTED
                ]
                expected_needs_review = [
                    claim_id
                    for claim_id in expected
                    if decision_by_id[claim_id].verdict
                    == CheckerVerdict.NEEDS_REVIEW
                ]
                if (
                    field_result.accepted_claim_ids != expected_accepted
                    or field_result.rejected_claim_ids != expected_rejected
                    or field_result.needs_review_claim_ids
                    != expected_needs_review
                ):
                    raise ValueError(
                        "Checker field decision partitions are inconsistent."
                    )
                if self.generated_by == "deterministic" and (
                    field_result.status == CheckerFieldStatus.VERIFIED
                    or (
                        expected
                        and field_result.status
                        != CheckerFieldStatus.NOT_REVIEWED
                    )
                ):
                    raise ValueError(
                        "Deterministic Checker cannot verify or semantically classify fields."
                    )

            statuses = [item.status for item in task_result.field_results]
            if all(status == CheckerFieldStatus.VERIFIED for status in statuses):
                expected_task_status = CheckerTaskStatus.VERIFIED
            elif any(status == CheckerFieldStatus.CONFLICTING for status in statuses):
                expected_task_status = CheckerTaskStatus.CONFLICTING
            elif any(status == CheckerFieldStatus.NOT_REVIEWED for status in statuses):
                expected_task_status = CheckerTaskStatus.NOT_REVIEWED
            elif all(
                status == CheckerFieldStatus.NOT_ACCESSIBLE for status in statuses
            ):
                expected_task_status = CheckerTaskStatus.NOT_ACCESSIBLE
            elif all(
                status
                in {
                    CheckerFieldStatus.MISSING,
                    CheckerFieldStatus.REJECTED,
                    CheckerFieldStatus.NOT_ACCESSIBLE,
                }
                for status in statuses
            ):
                expected_task_status = CheckerTaskStatus.MISSING
            else:
                expected_task_status = CheckerTaskStatus.PARTIAL
            if task_result.status != expected_task_status:
                raise ValueError("Checker task status is inconsistent with its fields.")

        follow_up_ids = [item.follow_up_id for item in self.follow_up_tasks]
        if len(follow_up_ids) != len(set(follow_up_ids)):
            raise ValueError("Checker follow-up IDs must be unique.")
        follow_up_by_id = {item.follow_up_id: item for item in self.follow_up_tasks}
        follow_up_by_key: dict[tuple[str, str], CheckerFollowUpTask] = {}
        for follow_up in self.follow_up_tasks:
            if (
                (follow_up.task_id, follow_up.target_field)
                not in field_result_by_key
                or not set(
                    [*follow_up.related_claim_ids, *follow_up.supporting_claim_ids]
                ).issubset(decision_by_id)
            ):
                raise ValueError("Checker follow-up scope is invalid.")
            key = (follow_up.task_id, follow_up.target_field)
            if key in follow_up_by_key:
                raise ValueError("Checker fields may have at most one follow-up.")
            follow_up_by_key[key] = follow_up
            field_claim_ids = set(field_result_by_key[key].raw_claim_ids)
            if self.schema_version == "1.0.0":
                if (
                    follow_up.related_claim_ids
                    != field_result_by_key[key].raw_claim_ids
                ):
                    raise ValueError(
                        "Checker follow-up claims must match its unresolved field."
                    )
                continue
            for values in (
                follow_up.related_claim_ids,
                follow_up.supporting_claim_ids,
                follow_up.candidate_source_ids,
                follow_up.retry_source_ids,
                follow_up.reextract_source_ids,
                follow_up.suggested_queries,
            ):
                if len(values) != len(set(values)):
                    raise ValueError("Checker follow-up lists must be unique.")
            if not set(
                [*follow_up.related_claim_ids, *follow_up.supporting_claim_ids]
            ).issubset(field_claim_ids):
                raise ValueError(
                    "Checker follow-up claims must belong to its target field."
                )
            if not set(follow_up.candidate_source_ids).issubset(
                self.unevaluated_source_ids
            ):
                raise ValueError(
                    "Checker candidate sources must be known unevaluated sources."
                )
            if not set(
                [*follow_up.retry_source_ids, *follow_up.reextract_source_ids]
            ).issubset(known_sources):
                raise ValueError(
                    "Checker retry and re-extraction sources must be selected sources."
                )
            if not set(
                [
                    *follow_up.candidate_source_ids,
                    *follow_up.retry_source_ids,
                    *follow_up.reextract_source_ids,
                ]
            ).issubset(known_all_sources):
                raise ValueError("Checker follow-up references an unknown source.")
        unresolved_field_keys = {
            key
            for key, result in field_result_by_key.items()
            if result.status != CheckerFieldStatus.VERIFIED
        }
        if set(follow_up_by_key) != unresolved_field_keys:
            raise ValueError(
                "Every unresolved Checker field requires exactly one follow-up."
            )
        for task_result in self.task_results:
            expected_task_follow_ups = [
                follow_up.follow_up_id
                for follow_up in self.follow_up_tasks
                if follow_up.task_id == task_result.task_id
            ]
            if (
                len(task_result.follow_up_ids)
                != len(set(task_result.follow_up_ids))
                or task_result.follow_up_ids != expected_task_follow_ups
                or any(
                    follow_up_by_id[item].task_id != task_result.task_id
                    for item in task_result.follow_up_ids
                )
            ):
                raise ValueError("Checker task follow-up mapping is invalid.")

        expected_critical_missing = [
            field_result.target_field
            for task_result in self.task_results
            if task_result.priority == Priority.CRITICAL
            for field_result in task_result.field_results
            if field_result.status != CheckerFieldStatus.VERIFIED
        ]
        if self.critical_missing_fields != expected_critical_missing:
            raise ValueError("Checker critical missing fields are inconsistent.")

        if self.quality_score != self.score_breakdown.quality_score:
            raise ValueError("Checker quality score must match score breakdown.")
        blocking_unsafe = any(
            item.severity in {CheckerSeverity.HIGH, CheckerSeverity.CRITICAL}
            for item in self.unsafe_items
        )
        expected_passed = (
            self.generated_by == "openai"
            and not self.failed_attempts
            and self.quality_score >= self.quality_threshold
            and self.scope_complete
            and not self.critical_missing_fields
            and not self.unevaluated_critical_fields
            and not self.contradictions
            and not blocking_unsafe
        )
        if self.passed != expected_passed:
            raise ValueError("Checker pass flag is inconsistent with quality gates.")

        if len(self.agent_usage) > self.limits.max_api_calls:
            raise ValueError("Checker usage exceeds max_api_calls.")
        if any(
            item.agent != "checker"
            or item.iteration != self.iteration
            or item.call_index != 1
            or item.scope_task_ids != self.selected_task_ids
            or item.scope_source_ids != self.selected_source_ids
            or item.tool_usage
            for item in self.agent_usage
        ):
            raise ValueError("Checker usage scope is inconsistent.")
        if len(self.failed_attempts) > 1:
            raise ValueError("Checker supports at most one provider attempt.")
        if self.failed_attempts:
            failure = self.failed_attempts[0]
            usage_recorded = bool(self.agent_usage)
            if (
                failure.scope_task_ids != self.selected_task_ids
                or failure.scope_source_ids != self.selected_source_ids
                or failure.usage_recorded != usage_recorded
                or failure.token_usage_unknown == usage_recorded
            ):
                raise ValueError("Checker failure ledger is inconsistent.")

        if self.generated_by == "deterministic":
            expected_action = CheckerNextAction.RUN_PAID_CHECKER
        elif self.failed_attempts:
            expected_action = CheckerNextAction.RETRY_CHECKER
        elif self.passed:
            expected_action = CheckerNextAction.HUMAN_REVIEW
        else:
            expected_action = CheckerNextAction.RESOLVE_GAPS
        if self.recommended_next_action != expected_action:
            raise ValueError("Checker recommended next action is inconsistent.")
        return self


class ResolverAction(StrEnum):
    EXTRACT_KNOWN_SOURCE = "extract_known_source"
    RETRY_RETRIEVAL = "retry_retrieval"
    REEXTRACT_EXISTING = "reextract_existing"
    SEARCH_NEW_SOURCE = "search_new_source"
    HUMAN_REVIEW = "human_review"


class ResolverStrategySource(StrEnum):
    DETERMINISTIC = "deterministic"
    OPENAI = "openai"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class ResolverNextAction(StrEnum):
    EXECUTE_RESOLUTION = "execute_resolution"
    HUMAN_REVIEW = "human_review"


class ResolverItemDraft(ClosedModel):
    follow_up_id: str = Field(pattern=r"^followup-[a-f0-9]{16}$")
    selected_action: ResolverAction
    selected_source_ids: list[str] = Field(default_factory=list, max_length=50)
    derived_queries: list[str] = Field(default_factory=list, max_length=5)
    sequence: int = Field(ge=1, le=500)
    rationale: str = Field(min_length=5, max_length=1000)

    @model_validator(mode="after")
    def validate_resolver_item_draft(self) -> "ResolverItemDraft":
        if len(self.selected_source_ids) != len(set(self.selected_source_ids)):
            raise ValueError("Resolver draft source IDs must be unique.")
        if len(self.derived_queries) != len(set(self.derived_queries)):
            raise ValueError("Resolver draft queries must be unique.")
        if any(
            not query.strip() or len(query) > 500 or "\x00" in query
            for query in self.derived_queries
        ):
            raise ValueError("Resolver draft queries must be bounded plain text.")
        return self


class ResolverDraft(ClosedModel):
    items: list[ResolverItemDraft] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_resolver_draft(self) -> "ResolverDraft":
        follow_up_ids = [item.follow_up_id for item in self.items]
        sequences = [item.sequence for item in self.items]
        if len(follow_up_ids) != len(set(follow_up_ids)):
            raise ValueError("Resolver draft follow-up IDs must be unique.")
        if len(sequences) != len(set(sequences)):
            raise ValueError("Resolver draft sequences must be unique.")
        if sorted(sequences) != list(range(1, len(sequences) + 1)):
            raise ValueError("Resolver draft sequences must be contiguous from one.")
        return self


class ResolverWorkItem(ClosedModel):
    resolution_item_id: str = Field(pattern=r"^resolution-item-[a-f0-9]{16}$")
    follow_up_id: str = Field(pattern=r"^followup-[a-f0-9]{16}$")
    task_id: str
    target_field: str
    priority: Priority
    reason: CheckerFollowUpReason
    sequence: int = Field(ge=1, le=500)
    allowed_actions: list[ResolverAction] = Field(min_length=1, max_length=5)
    selected_action: ResolverAction
    selected_source_ids: list[str] = Field(default_factory=list, max_length=50)
    fallback_source_ids: list[str] = Field(default_factory=list, max_length=100)
    queries: list[str] = Field(default_factory=list, max_length=10)
    related_claim_ids: list[str] = Field(default_factory=list, max_length=20)
    supporting_claim_ids: list[str] = Field(default_factory=list, max_length=20)
    minimum_additional_sources: int = Field(ge=0, le=20)
    requires_independent_source: bool
    completion_criteria: str = Field(min_length=10, max_length=2000)
    rationale: str = Field(min_length=5, max_length=1000)
    status: Literal["pending"] = "pending"

    @model_validator(mode="after")
    def validate_resolver_work_item(self) -> "ResolverWorkItem":
        for values in (
            self.allowed_actions,
            self.selected_source_ids,
            self.fallback_source_ids,
            self.queries,
            self.related_claim_ids,
            self.supporting_claim_ids,
        ):
            if len(values) != len(set(values)):
                raise ValueError("Resolver work-item lists must be unique.")
        if self.selected_action not in self.allowed_actions:
            raise ValueError("Resolver selected action is not locally allowed.")
        source_action = self.selected_action in {
            ResolverAction.EXTRACT_KNOWN_SOURCE,
            ResolverAction.RETRY_RETRIEVAL,
            ResolverAction.REEXTRACT_EXISTING,
        }
        if source_action != bool(self.selected_source_ids):
            raise ValueError(
                "Resolver source actions require sources and other actions forbid them."
            )
        if (
            self.selected_action == ResolverAction.SEARCH_NEW_SOURCE
            and not self.queries
        ):
            raise ValueError("Resolver search actions require at least one query.")
        if any(
            not query.strip() or len(query) > 500 or "\x00" in query
            for query in self.queries
        ):
            raise ValueError("Resolver queries must be bounded plain text.")
        return self


class ResolverExecutionBatch(ClosedModel):
    batch_id: str = Field(pattern=r"^resolution-batch-[a-f0-9]{16}$")
    action: ResolverAction
    resolution_item_ids: list[str] = Field(min_length=1, max_length=100)
    follow_up_ids: list[str] = Field(min_length=1, max_length=100)
    task_ids: list[str] = Field(min_length=1, max_length=100)
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    queries: list[str] = Field(default_factory=list, max_length=100)


class ResolverLimits(ClosedModel):
    max_follow_ups: int = Field(ge=1, le=100)
    max_source_actions: int = Field(ge=1, le=100)
    max_search_tasks: int = Field(ge=1, le=100)
    max_queries_per_item: int = Field(ge=1, le=10)
    max_api_calls: Literal[1] = 1


class ResolverAttemptFailure(ClosedModel):
    call_index: Literal[1] = 1
    scope_task_ids: list[str] = Field(min_length=1)
    scope_source_ids: list[str] = Field(default_factory=list)
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    usage_recorded: bool
    token_usage_unknown: bool = False


class ResolverResults(ClosedModel):
    """Bounded repair plan consumed by the next Searcher/Extractor round."""

    schema_version: Literal["1.0.0"] = RESOLVER_SCHEMA_VERSION
    prompt_version: Literal["resolver-system-v1"] = RESOLVER_PROMPT_VERSION
    resolution_id: str
    plan_run_id: str
    search_id: str
    extraction_id: str
    check_id: str
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    search_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    extraction_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    check_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_reference: str = Field(min_length=1)
    search_reference: str = Field(min_length=1)
    extraction_reference: str = Field(min_length=1)
    check_reference: str = Field(min_length=1)
    created_at: datetime
    iteration: int = Field(ge=1)
    generated_by: Literal["deterministic", "openai"]
    strategy_source: ResolverStrategySource
    model: str | None
    provider_executed: bool
    brand_name: str
    target_country: str = Field(pattern=r"^[A-Z]{2}$")
    depth: ResearchDepth
    limits: ResolverLimits
    available_source_ids: list[str]
    selected_follow_up_ids: list[str]
    deferred_follow_up_ids: list[str]
    work_items: list[ResolverWorkItem]
    execution_batches: list[ResolverExecutionBatch]
    execution_source_ids: list[str]
    search_task_ids: list[str]
    ready_for_execution: bool
    recommended_next_action: ResolverNextAction
    warnings: list[str]
    compliance_rules: list[str]
    agent_usage: list[AgentIterationUsage] = Field(default_factory=list)
    failed_attempts: list[ResolverAttemptFailure] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_resolver_results(self) -> "ResolverResults":
        for value, field_name in (
            (self.resolution_id, "resolution_id"),
            (self.plan_run_id, "plan_run_id"),
            (self.search_id, "search_id"),
            (self.extraction_id, "extraction_id"),
            (self.check_id, "check_id"),
        ):
            try:
                parsed = UUID(value)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"{field_name} must be a valid UUIDv4.") from exc
            if parsed.version != 4:
                raise ValueError(f"{field_name} must be a valid UUIDv4.")

        for values, field_name in (
            (self.available_source_ids, "available_source_ids"),
            (self.selected_follow_up_ids, "selected_follow_up_ids"),
            (self.deferred_follow_up_ids, "deferred_follow_up_ids"),
            (self.execution_source_ids, "execution_source_ids"),
            (self.search_task_ids, "search_task_ids"),
            (self.warnings, "warnings"),
            (self.compliance_rules, "compliance_rules"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"Resolver {field_name} values must be unique.")
        if set(self.selected_follow_up_ids) & set(self.deferred_follow_up_ids):
            raise ValueError("Resolver selected and deferred follow-ups overlap.")
        if any(
            not re.fullmatch(r"source-[a-f0-9]{16}", source_id)
            for source_id in self.available_source_ids
        ):
            raise ValueError("Resolver available source ID is invalid.")
        if any(
            not re.fullmatch(r"followup-[a-f0-9]{16}", follow_up_id)
            for follow_up_id in [
                *self.selected_follow_up_ids,
                *self.deferred_follow_up_ids,
            ]
        ):
            raise ValueError("Resolver follow-up ID is invalid.")
        if [item.follow_up_id for item in self.work_items] != self.selected_follow_up_ids:
            raise ValueError("Resolver work items must follow selected follow-up order.")
        sequences = [item.sequence for item in self.work_items]
        if sorted(sequences) != list(range(1, len(sequences) + 1)):
            raise ValueError("Resolver work-item sequence is invalid.")
        if len({item.resolution_item_id for item in self.work_items}) != len(
            self.work_items
        ):
            raise ValueError("Resolver work-item IDs must be unique.")
        if any(
            not set([*item.selected_source_ids, *item.fallback_source_ids]).issubset(
                self.available_source_ids
            )
            for item in self.work_items
        ):
            raise ValueError("Resolver work item references an unavailable source.")

        item_by_id = {item.resolution_item_id: item for item in self.work_items}
        batched_item_ids = [
            item_id
            for batch in self.execution_batches
            for item_id in batch.resolution_item_ids
        ]
        if len(batched_item_ids) != len(set(batched_item_ids)) or set(
            batched_item_ids
        ) != set(item_by_id):
            raise ValueError("Resolver batches must partition work items exactly.")
        if len({batch.batch_id for batch in self.execution_batches}) != len(
            self.execution_batches
        ):
            raise ValueError("Resolver batch IDs must be unique.")
        for batch in self.execution_batches:
            items = [item_by_id[item_id] for item_id in batch.resolution_item_ids]
            if (
                any(item.selected_action != batch.action for item in items)
                or batch.follow_up_ids != [item.follow_up_id for item in items]
                or batch.task_ids
                != list(dict.fromkeys(item.task_id for item in items))
                or batch.source_ids
                != list(
                    dict.fromkeys(
                        source_id
                        for item in items
                        for source_id in item.selected_source_ids
                    )
                )
                or batch.queries
                != list(
                    dict.fromkeys(query for item in items for query in item.queries)
                )
            ):
                raise ValueError("Resolver batch does not match its work items.")
        expected_execution_sources = list(
            dict.fromkeys(
                source_id
                for item in self.work_items
                for source_id in item.selected_source_ids
            )
        )
        if self.execution_source_ids != expected_execution_sources:
            raise ValueError("Resolver execution source summary is inconsistent.")
        expected_search_tasks = list(
            dict.fromkeys(
                item.task_id
                for item in self.work_items
                if item.selected_action == ResolverAction.SEARCH_NEW_SOURCE
            )
        )
        if self.search_task_ids != expected_search_tasks:
            raise ValueError("Resolver search-task summary is inconsistent.")
        if len(self.execution_source_ids) > self.limits.max_source_actions:
            raise ValueError("Resolver exceeds max_source_actions.")
        if len(self.search_task_ids) > self.limits.max_search_tasks:
            raise ValueError("Resolver exceeds max_search_tasks.")
        if len(self.work_items) > self.limits.max_follow_ups:
            raise ValueError("Resolver exceeds max_follow_ups.")

        if self.generated_by == "deterministic":
            if (
                self.model is not None
                or self.provider_executed
                or self.strategy_source != ResolverStrategySource.DETERMINISTIC
                or self.agent_usage
                or self.failed_attempts
            ):
                raise ValueError("Deterministic Resolver has invalid provider state.")
        else:
            if self.model is None or not self.model.strip() or not self.provider_executed:
                raise ValueError("Paid Resolver must record its model and attempt.")
            if len(self.agent_usage) > 1 or len(self.failed_attempts) > 1:
                raise ValueError("Resolver supports at most one provider attempt.")
            if bool(self.failed_attempts) == (
                self.strategy_source == ResolverStrategySource.OPENAI
            ):
                raise ValueError("Resolver strategy source conflicts with provider result.")
            if not self.failed_attempts and len(self.agent_usage) != 1:
                raise ValueError("Successful paid Resolver requires one usage entry.")
        expected_scope_task_ids = list(
            dict.fromkeys(item.task_id for item in self.work_items)
        )
        if any(
            usage.agent != "resolver"
            or usage.iteration != self.iteration
            or usage.call_index != 1
            or usage.scope_task_ids != expected_scope_task_ids
            or usage.scope_source_ids != self.available_source_ids
            or usage.tool_usage
            for usage in self.agent_usage
        ):
            raise ValueError("Resolver usage metadata is inconsistent.")
        if self.failed_attempts:
            failure = self.failed_attempts[0]
            usage_recorded = bool(self.agent_usage)
            if (
                failure.scope_task_ids != expected_scope_task_ids
                or failure.scope_source_ids != self.available_source_ids
                or failure.usage_recorded != usage_recorded
                or failure.token_usage_unknown == usage_recorded
            ):
                raise ValueError("Resolver failure ledger is inconsistent.")
        expected_ready = bool(self.work_items)
        if self.ready_for_execution != expected_ready:
            raise ValueError("Resolver ready flag is inconsistent.")
        expected_action = (
            ResolverNextAction.EXECUTE_RESOLUTION
            if expected_ready
            else ResolverNextAction.HUMAN_REVIEW
        )
        if self.recommended_next_action != expected_action:
            raise ValueError("Resolver next action is inconsistent.")
        return self
