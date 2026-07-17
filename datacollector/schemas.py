"""Versioned contracts shared by the Planner and future loop agents."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from string import Formatter
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SCHEMA_VERSION = "1.2.0"
PROMPT_VERSION = "planner-system-v2"
SEARCHER_SCHEMA_VERSION = "1.0.0"
SEARCHER_PROMPT_VERSION = "searcher-system-v1"


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
    """Usage ledger entry for a charged provider response that could not be used."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    failure_id: str
    plan_run_id: str
    created_at: datetime
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    usage: AgentIterationUsage

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
    NO_SOURCES_FOUND = "no_sources_found"
    NOT_SEARCHED = "not_searched"


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


class SearchAction(ClosedModel):
    action_id: str | None = None
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
    provider_verified: bool
    task_ids: list[str] = Field(default_factory=list)
    discovered_via_queries: list[str] = Field(default_factory=list)
    relevance_note: str = Field(default="", max_length=1000)
    discovered_at: datetime


class SearchTaskResult(ClosedModel):
    task_id: str
    catalog_question_id: str
    status: SearchTaskStatus
    planned_queries: list[str]
    attempted_queries: list[str]
    source_ids: list[str]
    notes: str = Field(default="", max_length=1000)


class SearchResults(ClosedModel):
    """Auditable source-discovery artifact consumed later by Extractor."""

    schema_version: Literal["1.0.0"] = SEARCHER_SCHEMA_VERSION
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
            if source.provider_verified != (
                source.origin == SearchSourceOrigin.OPENAI_WEB_SEARCH
            ):
                raise ValueError(
                    "Search source origin must match provider verification status."
                )
        for result in self.task_results:
            for values, field_name in (
                (result.planned_queries, "planned_queries"),
                (result.attempted_queries, "attempted_queries"),
                (result.source_ids, "source_ids"),
            ):
                if len(values) != len(set(values)):
                    raise ValueError(f"Task result {field_name} values must be unique.")
            if not set(result.source_ids).issubset(known_sources):
                raise ValueError("Task results reference unknown source IDs.")
            if result.status == SearchTaskStatus.SOURCES_FOUND and not result.source_ids:
                raise ValueError("sources_found task results require source IDs.")
            if result.status == SearchTaskStatus.NO_SOURCES_FOUND and (
                not result.attempted_queries or result.source_ids
            ):
                raise ValueError(
                    "no_sources_found requires attempted queries and no sources."
                )
            if result.status == SearchTaskStatus.QUERY_WORKLOAD_ONLY and (
                result.attempted_queries or result.source_ids
            ):
                raise ValueError(
                    "query_workload_only cannot contain attempts or sources."
                )
            for source_id in result.source_ids:
                if result.task_id not in source_by_id[source_id].task_ids:
                    raise ValueError(
                        "Task/source mappings must be symmetric in search results."
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

        if self.generated_by == "offline":
            if self.model is not None or self.agent_usage or self.search_executed:
                raise ValueError(
                    "Free Searcher cannot declare a model, usage, or executed search."
                )
            if self.actions or any(source.provider_verified for source in self.sources):
                raise ValueError(
                    "Free Searcher cannot contain provider actions or verified sources."
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
            recorded_search_calls = sum(
                tool.calls
                for usage in self.agent_usage
                for tool in usage.tool_usage
                if tool.tool == "web_search"
            )
            if observed_search_calls != recorded_search_calls:
                raise ValueError(
                    "Recorded web search tool calls must match search actions."
                )
        return self
