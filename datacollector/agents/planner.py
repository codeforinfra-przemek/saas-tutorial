"""Planner agent: turn a canonical question bank into an auditable plan."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..catalog import select_questions
from ..llm.protocol import PlannerLLM
from ..profiles import (
    ResearchProfileCatalog,
    load_profile_catalog,
    materialize_profile,
)
from ..query_utils import normalize_search_queries
from ..schemas import (
    PRIORITY_ORDER,
    AgentIterationUsage,
    CatalogQuestion,
    PlannerDraft,
    PlannerInput,
    Priority,
    QuestionCatalog,
    Requirement,
    ResearchPlan,
    ResearchProfileSnapshot,
    ResearchTask,
    StopConditions,
    TaskAction,
)


DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "planner_system_v2.md"
)
UNRESOLVED_QUERY_MARKER = re.compile(r"\{[^{}]+\}|\[[^\[\]]+\]|<[^<>]+>")


class PlannerValidationError(ValueError):
    """Raised if LLM guidance tries to escape the canonical task catalog."""


DEFAULT_PRIORITY = {
    Requirement.CRITICAL: Priority.CRITICAL,
    Requirement.REQUIRED: Priority.HIGH,
    Requirement.RECOMMENDED: Priority.MEDIUM,
    Requirement.OPTIONAL: Priority.LOW,
}


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _render_queries(question: CatalogQuestion, planner_input: PlannerInput) -> list[str]:
    context = {
        "brand": planner_input.brand_name,
        "country": planner_input.target_country,
        "regions": ", ".join(planner_input.target_regions) or planner_input.target_country,
        "legal_name": planner_input.known_legal_name or planner_input.brand_name,
    }
    return [template.format_map(context) for template in question.search_query_templates]


def _stronger_priority(first: Priority, second: Priority) -> Priority:
    return first if PRIORITY_ORDER[first] >= PRIORITY_ORDER[second] else second


def _is_executable_query(query: str) -> bool:
    return not UNRESOLVED_QUERY_MARKER.search(query)


class PlannerAgent:
    """Build a complete plan; an LLM may enrich but never define its coverage."""

    def __init__(
        self,
        catalog: QuestionCatalog,
        llm: PlannerLLM | None = None,
        *,
        prompt_path: Path | str = DEFAULT_PROMPT_PATH,
        profile_catalog: ResearchProfileCatalog | None = None,
    ):
        self.catalog = catalog
        self.llm = llm
        self.prompt_path = Path(prompt_path)
        self.profile_catalog = profile_catalog

    def create_plan(
        self, planner_input: PlannerInput, *, iteration: int = 1
    ) -> ResearchPlan:
        if iteration < 1:
            raise PlannerValidationError("Planner iteration must be at least 1.")
        profile_snapshot: ResearchProfileSnapshot | None = None
        effective_input = planner_input
        if planner_input.profile_id is not None:
            profile_catalog = self.profile_catalog or load_profile_catalog()
            try:
                profile_snapshot, selected = materialize_profile(
                    profile_catalog,
                    self.catalog,
                    planner_input.profile_id,
                    allow_personal_data=planner_input.allow_personal_data,
                )
            except ValueError as exc:
                raise PlannerValidationError(str(exc)) from exc
            if profile_snapshot.country != planner_input.target_country:
                raise PlannerValidationError(
                    f"Profile {profile_snapshot.profile_id} is for "
                    f"{profile_snapshot.country}, not {planner_input.target_country}."
                )
            effective_input = planner_input.model_copy(
                update={
                    "profile_id": profile_snapshot.profile_id,
                    "depth": profile_snapshot.legacy_depth,
                }
            )
        else:
            selected = select_questions(self.catalog, planner_input)
        if not selected:
            raise PlannerValidationError("No catalog questions match the requested scope.")

        selected_ids = {question.id for _, question in selected}
        missing_dependencies = {
            question.id: sorted(set(question.dependencies) - selected_ids)
            for _, question in selected
            if set(question.dependencies) - selected_ids
        }
        if missing_dependencies:
            raise PlannerValidationError(
                "Selected catalog questions have unavailable dependencies: "
                f"{missing_dependencies}"
            )

        # Validate deterministic catalog coverage before making a paid API call.
        draft, agent_usage = self._create_draft(
            effective_input,
            [item[1] for item in selected],
            iteration=iteration,
        )
        unknown_ids = {
            guidance.catalog_question_id
            for guidance in draft.task_guidance
            if guidance.catalog_question_id not in selected_ids
        }
        if unknown_ids:
            raise PlannerValidationError(
                f"LLM guidance referenced unknown questions: {sorted(unknown_ids)}"
            )

        guidance_by_id = {
            guidance.catalog_question_id: guidance
            for guidance in draft.task_guidance
        }
        if profile_snapshot is not None:
            catalog_ordinals = {
                question.id: index
                for index, question in enumerate(
                    self.catalog.all_questions(), start=1
                )
            }
            task_id_by_question = {
                question.id: (
                    f"task-{catalog_ordinals[question.id]:03d}-"
                    f"{question.id.replace('.', '-')}"
                )
                for _, question in selected
            }
        else:
            task_id_by_question = {
                question.id: f"task-{index:03d}-{question.id.replace('.', '-')}"
                for index, (_, question) in enumerate(selected, start=1)
            }
        existing_fields = set(effective_input.existing_fields)
        tasks: list[ResearchTask] = []
        filtered_guidance_query_count = 0
        normalized_query_count = 0

        for section_id, question in selected:
            guidance = guidance_by_id.get(question.id)
            base_priority = DEFAULT_PRIORITY[question.requirement]
            priority = (
                _stronger_priority(base_priority, guidance.priority)
                if guidance
                else base_priority
            )
            canonical_queries = _render_queries(question, effective_input)
            raw_guided_queries = guidance.search_queries if guidance else []
            guided_queries = [
                query for query in raw_guided_queries if _is_executable_query(query)
            ]
            filtered_guidance_query_count += len(raw_guided_queries) - len(
                guided_queries
            )
            search_queries, normalized_count = normalize_search_queries(
                canonical_queries + guided_queries
            )
            normalized_query_count += normalized_count
            search_queries = search_queries[: effective_input.max_queries_per_task]
            fields_to_verify = [
                field for field in question.target_fields if field in existing_fields
            ]
            fields_to_collect = [
                field for field in question.target_fields if field not in existing_fields
            ]
            if fields_to_collect and fields_to_verify:
                action = TaskAction.COLLECT_AND_VERIFY
            elif fields_to_collect:
                action = TaskAction.COLLECT
            else:
                action = TaskAction.VERIFY
            rationale = (
                guidance.rationale
                if guidance
                else (
                    f"Required by research profile {profile_snapshot.profile_id}."
                    if profile_snapshot is not None
                    else "Required by the versioned franchise research catalog."
                )
            )
            tasks.append(
                ResearchTask(
                    task_id=task_id_by_question[question.id],
                    catalog_question_id=question.id,
                    section_id=section_id,
                    title=question.title,
                    question=question.question,
                    fdd_items=question.fdd_items,
                    priority=priority,
                    requirement=question.requirement,
                    action=action,
                    target_fields=question.target_fields,
                    fields_to_collect=fields_to_collect,
                    fields_to_verify=fields_to_verify,
                    preferred_source_types=question.evidence.preferred_source_types,
                    source_hints=(
                        _deduplicate(guidance.source_hints) if guidance else []
                    ),
                    search_queries=search_queries,
                    acceptance_criteria=question.evidence.acceptance_criteria,
                    min_sources=question.evidence.min_sources,
                    requires_independent_corroboration=(
                        question.evidence.requires_independent_corroboration
                    ),
                    max_age_days=question.evidence.max_age_days,
                    depends_on=[
                        task_id_by_question[dependency]
                        for dependency in question.dependencies
                    ],
                    sensitivity=question.sensitivity,
                    rationale=rationale,
                )
            )

        if profile_snapshot is not None:
            critical_fields = _deduplicate(
                [
                    field.target_field
                    for question in profile_snapshot.questions
                    for field in question.fields
                    if field.required_for_completion
                ]
            )
        else:
            critical_fields = _deduplicate(
                [
                    field
                    for task in tasks
                    if task.priority == Priority.CRITICAL
                    for field in task.target_fields
                ]
            )
        scope_warnings = _deduplicate(
            [self.catalog.legal_note, *draft.scope_warnings]
        )
        planning_notes = list(draft.planning_notes)
        if profile_snapshot is not None:
            planning_notes.append(
                f"Applied research profile {profile_snapshot.profile_id}; field "
                "availability policies are frozen in profile_snapshot."
            )
        if filtered_guidance_query_count:
            planning_notes.append(
                "Removed "
                f"{filtered_guidance_query_count} LLM guidance queries containing "
                "unresolved placeholders; canonical executable queries remain."
            )
        if normalized_query_count:
            planning_notes.append(
                "Normalized "
                f"{normalized_query_count} search queries containing duplicate "
                "adjacent terms or duplicate query variants."
            )
        compliance_rules = [
            *self.catalog.source_policy.rules,
            *[
                f"PROHIBITED: {method}"
                for method in self.catalog.source_policy.prohibited_methods
            ],
            "Do not publish or import researched facts before human review.",
            "Keep source reliability separate from confidence in the claim.",
        ]

        return ResearchPlan(
            catalog_version=self.catalog.version,
            run_id=str(uuid4()),
            created_at=datetime.now(timezone.utc),
            generated_by="openai" if self.llm else "offline",
            model=self.llm.model_name if self.llm else None,
            planner_input=effective_input,
            profile_snapshot=profile_snapshot,
            objective=draft.objective,
            planning_notes=planning_notes,
            assumptions=draft.assumptions,
            scope_warnings=scope_warnings,
            tasks=tasks,
            critical_fields=critical_fields,
            stop_conditions=StopConditions(
                quality_threshold=effective_input.quality_threshold,
                max_rounds=effective_input.max_rounds,
            ),
            authoritative_sources=(
                profile_snapshot.country_authoritative_sources
                if profile_snapshot is not None
                else self.catalog.authoritative_sources
            ),
            source_policy=self.catalog.source_policy,
            compliance_rules=_deduplicate(compliance_rules),
            agent_usage=agent_usage,
        )

    def _create_draft(
        self,
        planner_input: PlannerInput,
        questions: list[CatalogQuestion],
        *,
        iteration: int,
    ) -> tuple[PlannerDraft, list[AgentIterationUsage]]:
        if self.llm:
            try:
                system_prompt = self.prompt_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise PlannerValidationError(
                    f"Cannot load Planner prompt: {self.prompt_path}"
                ) from exc
            generation = self.llm.generate(
                planner_input,
                questions,
                system_prompt,
                iteration=iteration,
            )
            return generation.draft, [generation.usage]

        return PlannerDraft(
            objective=(
                "Create an auditable "
                f"{planner_input.profile_id or planner_input.depth.value} research "
                f"plan for {planner_input.brand_name} in "
                f"{planner_input.target_country}."
            ),
            planning_notes=[
                "Offline mode: canonical priorities and query templates were used.",
                "Run without --offline to let OpenAI tailor priorities and queries.",
            ],
            assumptions=[],
            scope_warnings=[],
            task_guidance=[],
        ), []
