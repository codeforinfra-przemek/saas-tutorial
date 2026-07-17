"""Versioned contracts shared by the Planner and future loop agents."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from string import Formatter
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "1.1.0"
PROMPT_VERSION = "planner-system-v2"


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
    COURT = "court"
    LEGAL_DOCUMENT = "legal_document"
    AUDITED_FINANCIAL = "audited_financial"
    REPUTABLE_MEDIA = "reputable_media"
    INDUSTRY = "industry"
    FRANCHISEE_INTERVIEW = "franchisee_interview"
    REVIEW_PLATFORM = "review_platform"
    SOCIAL = "social"


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
    total_estimated_cost_usd: Decimal = Field(ge=0)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cost_total(self) -> "CostEstimate":
        expected = (
            self.uncached_input_cost_usd
            + self.cached_input_cost_usd
            + self.cache_write_input_cost_usd
            + self.output_cost_usd
        )
        if self.total_estimated_cost_usd != expected:
            raise ValueError("total_estimated_cost_usd must equal its components.")
        return self


class AgentIterationUsage(ClosedModel):
    """Usage and estimated cost for one logical iteration of one agent."""

    agent: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    iteration: int = Field(ge=1)
    provider: Literal["openai"] = "openai"
    requested_model: str = Field(min_length=1)
    resolved_model: str = Field(min_length=1)
    response_id: str | None = None
    request_id: str | None = None
    service_tier: str | None = None
    tokens: TokenUsage
    cost_estimate: CostEstimate | None = None


class ResearchPlan(ClosedModel):
    schema_version: Literal["1.0.0", "1.1.0"] = SCHEMA_VERSION
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
            self.schema_version == "1.1.0"
            and self.generated_by == "openai"
            and not self.agent_usage
        ):
            raise ValueError("OpenAI-generated schema 1.1 plans must contain usage.")

        usage_keys = [(item.agent, item.iteration) for item in self.agent_usage]
        if len(usage_keys) != len(set(usage_keys)):
            raise ValueError("Agent usage entries must be unique per agent iteration.")

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
