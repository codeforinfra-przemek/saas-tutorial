"""Versioned research profiles layered over the historical question catalog."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from .catalog import US_FRANCHISE_RULE_JURISDICTIONS
from .schemas import (
    DEPTH_ORDER,
    CatalogQuestion,
    ClosedModel,
    EvidenceRule,
    FieldAvailability,
    Jurisdiction,
    ProfileAccessScope,
    ProfileReuseScope,
    QuestionCatalog,
    Requirement,
    ResearchDepth,
    ResearchLevel,
    ResearchProfileFieldPolicy,
    ResearchProfileQuestionSnapshot,
    ResearchProfileSnapshot,
    Sensitivity,
    SourceType,
)


DEFAULT_PROFILE_CATALOG_PATH = (
    Path(__file__).resolve().parent / "catalogs" / "research_profiles_v2.yaml"
)

_REQUIREMENT_ORDER = {
    Requirement.OPTIONAL: 1,
    Requirement.RECOMMENDED: 2,
    Requirement.REQUIRED: 3,
    Requirement.CRITICAL: 4,
}


class ProfileCatalogError(ValueError):
    """Raised when profile configuration cannot be loaded or materialized."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects silently overwritten policy keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class ProfileQuestionRule(ClosedModel):
    question_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]+$")
    include_all_catalog_fields: bool = False
    fields: dict[str, FieldAvailability] = Field(default_factory=dict)
    exclude_fields: list[str] = Field(default_factory=list)
    requirement: Requirement | None = None
    default_availability: FieldAvailability | None = None
    title: str | None = Field(default=None, min_length=3, max_length=500)
    question: str | None = Field(default=None, min_length=10, max_length=4000)
    search_query_templates: list[str] | None = None
    dependencies: list[str] | None = None
    use_catalog_dependencies: bool = False
    use_catalog_evidence: bool = False
    min_sources: int | None = Field(default=None, ge=1, le=10)
    preferred_source_types: list[SourceType] | None = None
    acceptance_criteria: str | None = Field(
        default=None, min_length=10, max_length=4000
    )
    requires_independent_corroboration: bool | None = None
    max_age_days: int | None = Field(default=None, ge=1)
    clear_max_age_days: bool = False
    reuse_scope: ProfileReuseScope | None = None

    @field_validator("fields")
    @classmethod
    def validate_field_names(
        cls, values: dict[str, FieldAvailability]
    ) -> dict[str, FieldAvailability]:
        import re

        if any(
            re.fullmatch(r"[a-z][a-z0-9_.-]+", field_name) is None
            for field_name in values
        ):
            raise ValueError("Profile fields must be machine-readable dotted names.")
        return values

    @field_validator("exclude_fields")
    @classmethod
    def validate_excluded_field_names(cls, values: list[str]) -> list[str]:
        import re

        if len(values) != len(set(values)):
            raise ValueError("Excluded profile fields must be unique.")
        if any(
            re.fullmatch(r"[a-z][a-z0-9_.-]+", field_name) is None
            for field_name in values
        ):
            raise ValueError("Excluded profile fields must be dotted names.")
        return values

    @model_validator(mode="after")
    def validate_rule(self) -> "ProfileQuestionRule":
        if self.use_catalog_dependencies and self.dependencies is not None:
            raise ValueError(
                "A profile rule cannot set and reset dependencies together."
            )
        has_evidence_override = any(
            getattr(self, field_name) is not None
            for field_name in (
                "min_sources",
                "preferred_source_types",
                "acceptance_criteria",
                "requires_independent_corroboration",
                "max_age_days",
            )
        ) or self.clear_max_age_days
        if self.use_catalog_evidence and has_evidence_override:
            raise ValueError(
                "A profile rule cannot reset and override evidence together."
            )
        if self.max_age_days is not None and self.clear_max_age_days:
            raise ValueError(
                "A profile rule cannot set and clear max_age_days together."
            )
        overlap = set(self.fields) & set(self.exclude_fields)
        if overlap:
            raise ValueError(
                f"Profile rule cannot include and exclude fields together: {overlap}."
            )
        return self


class ResearchProfileDefinition(ClosedModel):
    profile_id: str = Field(pattern=r"^[A-Z]{2}:L[1-3]:v[1-9][0-9]*$")
    aliases: list[str] = Field(default_factory=list)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    level: ResearchLevel
    name: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=2000)
    intended_use: str = Field(min_length=10, max_length=2000)
    access_scope: ProfileAccessScope = ProfileAccessScope.PUBLIC
    legacy_depth: ResearchDepth
    inherits: str | None = None
    include_depth: ResearchDepth | None = None
    reset_catalog_definition_for_included_questions: bool = False
    exclude_question_ids: list[str] = Field(default_factory=list)
    default_availability: FieldAvailability
    country_authoritative_sources: list[str] = Field(default_factory=list)
    completion_required_availabilities: list[FieldAvailability] = Field(
        min_length=1
    )
    question_rules: list[ProfileQuestionRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity(self) -> "ResearchProfileDefinition":
        country, level, _ = self.profile_id.split(":")
        if country != self.country or level != self.level.value:
            raise ValueError("Profile ID must match country and level.")
        if self.profile_id in self.aliases:
            raise ValueError("Canonical profile ID cannot also be an alias.")
        rule_ids = [rule.question_id for rule in self.question_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("Profile question rules must be unique.")
        if len(self.exclude_question_ids) != len(set(self.exclude_question_ids)):
            raise ValueError("Excluded profile question IDs must be unique.")
        if len(self.completion_required_availabilities) != len(
            set(self.completion_required_availabilities)
        ):
            raise ValueError(
                "Profile completion-required availabilities must be unique."
            )
        if len(self.country_authoritative_sources) != len(
            set(self.country_authoritative_sources)
        ):
            raise ValueError("Profile country authorities must be unique.")
        if any(
            not source.startswith("https://")
            for source in self.country_authoritative_sources
        ):
            raise ValueError("Profile country authorities must use HTTPS.")
        return self


class ResearchProfileCatalog(ClosedModel):
    version: str = Field(min_length=1)
    title: str = Field(min_length=3)
    question_catalog_version: str = Field(min_length=1)
    profiles: list[ResearchProfileDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_profiles(self) -> "ResearchProfileCatalog":
        profile_ids = [profile.profile_id for profile in self.profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("Profile IDs must be unique.")
        known_ids = set(profile_ids)
        aliases = [alias.upper() for profile in self.profiles for alias in profile.aliases]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Profile aliases must be unique.")
        if set(alias.upper() for alias in profile_ids) & set(aliases):
            raise ValueError("Profile aliases cannot shadow canonical IDs.")
        if any(
            profile.inherits is not None and profile.inherits not in known_ids
            for profile in self.profiles
        ):
            raise ValueError("Profile inheritance references an unknown profile.")

        by_id = {profile.profile_id: profile for profile in self.profiles}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(profile_id: str) -> None:
            if profile_id in visiting:
                raise ValueError("Profile inheritance contains a cycle.")
            if profile_id in visited:
                return
            visiting.add(profile_id)
            parent_id = by_id[profile_id].inherits
            if parent_id is not None:
                parent = by_id[parent_id]
                child = by_id[profile_id]
                if parent.country != child.country or int(parent.level.value[1]) >= int(
                    child.level.value[1]
                ):
                    raise ValueError(
                        "Profiles may inherit only a lower level from the same country."
                    )
                visit(parent_id)
            visiting.remove(profile_id)
            visited.add(profile_id)

        for profile_id in profile_ids:
            visit(profile_id)
        return self


def load_profile_catalog(
    path: Path | str = DEFAULT_PROFILE_CATALOG_PATH,
) -> ResearchProfileCatalog:
    profile_path = Path(path)
    try:
        raw_data = yaml.load(
            profile_path.read_text(encoding="utf-8"),
            Loader=_UniqueKeySafeLoader,
        )
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileCatalogError(
            f"Cannot load research profile catalog: {profile_path}"
        ) from exc
    try:
        return ResearchProfileCatalog.model_validate(raw_data)
    except ValidationError as exc:
        raise ProfileCatalogError(
            f"Research profile catalog is invalid: {profile_path}"
        ) from exc


def _normalized_profile_id(value: str) -> str:
    parts = [part.strip() for part in value.split(":")]
    if len(parts) not in {2, 3}:
        return value.strip()
    normalized = [parts[0].upper(), parts[1].upper()]
    if len(parts) == 3:
        normalized.append(parts[2].lower())
    return ":".join(normalized)


def resolve_profile_definition(
    profile_catalog: ResearchProfileCatalog, profile_id: str
) -> ResearchProfileDefinition:
    requested = _normalized_profile_id(profile_id)
    for profile in profile_catalog.profiles:
        if requested == profile.profile_id or requested in {
            _normalized_profile_id(alias) for alias in profile.aliases
        }:
            return profile
    raise ProfileCatalogError(f"Unknown research profile: {profile_id}.")


def _question_is_applicable(
    question: CatalogQuestion, *, country: str, allow_personal_data: bool
) -> bool:
    if (
        question.jurisdiction == Jurisdiction.US_ONLY
        and country not in US_FRANCHISE_RULE_JURISDICTIONS
    ):
        return False
    if question.sensitivity == Sensitivity.PERSONAL_DATA and not allow_personal_data:
        return False
    return True


def _base_state(question: CatalogQuestion, section_id: str) -> dict[str, Any]:
    return {
        "section_id": section_id,
        "base": question,
        "title": question.title,
        "question": question.question,
        "requirement": question.requirement,
        "fields": {},
        "excluded_fields": set(),
        "evidence": question.evidence,
        "search_query_templates": list(question.search_query_templates),
        "dependencies": list(question.dependencies),
        "reuse_scope": ProfileReuseScope.BRAND,
    }


def _reset_catalog_definition(state: dict[str, Any]) -> None:
    """Restore canonical semantics while retaining selected field policies."""

    base: CatalogQuestion = state["base"]
    inherited_evidence: EvidenceRule = state["evidence"]
    base_evidence = base.evidence
    ages = [
        age
        for age in (inherited_evidence.max_age_days, base_evidence.max_age_days)
        if age is not None
    ]
    merged_evidence = EvidenceRule(
        min_sources=max(
            inherited_evidence.min_sources,
            base_evidence.min_sources,
        ),
        preferred_source_types=list(
            dict.fromkeys(
                [
                    *base_evidence.preferred_source_types,
                    *inherited_evidence.preferred_source_types,
                ]
            )
        ),
        acceptance_criteria=base_evidence.acceptance_criteria,
        requires_independent_corroboration=(
            inherited_evidence.requires_independent_corroboration
            or base_evidence.requires_independent_corroboration
        ),
        max_age_days=min(ages) if ages else None,
    )
    inherited_requirement: Requirement = state["requirement"]
    requirement = (
        inherited_requirement
        if _REQUIREMENT_ORDER[inherited_requirement]
        >= _REQUIREMENT_ORDER[base.requirement]
        else base.requirement
    )
    state.update(
        {
            "title": base.title,
            "question": base.question,
            "requirement": requirement,
            "evidence": merged_evidence,
            "dependencies": list(base.dependencies),
        }
    )


def _apply_rule(
    state: dict[str, Any],
    rule: ProfileQuestionRule,
    *,
    profile_default_availability: FieldAvailability,
) -> None:
    base: CatalogQuestion = state["base"]
    default_availability = rule.default_availability or profile_default_availability
    if rule.include_all_catalog_fields:
        for target_field in base.target_fields:
            if target_field not in state["excluded_fields"]:
                state["fields"].setdefault(target_field, default_availability)
    state["fields"].update(rule.fields)
    state["excluded_fields"].update(rule.exclude_fields)
    for target_field in rule.exclude_fields:
        state["fields"].pop(target_field, None)
    if rule.requirement is not None:
        state["requirement"] = rule.requirement
    if rule.title is not None:
        state["title"] = rule.title
    if rule.question is not None:
        state["question"] = rule.question
    if rule.search_query_templates is not None:
        state["search_query_templates"] = list(rule.search_query_templates)
    if rule.use_catalog_dependencies:
        state["dependencies"] = list(base.dependencies)
    elif rule.dependencies is not None:
        state["dependencies"] = list(rule.dependencies)
    if rule.use_catalog_evidence:
        state["evidence"] = base.evidence
    else:
        evidence: EvidenceRule = state["evidence"]
        updates: dict[str, Any] = {}
        for field_name in (
            "min_sources",
            "preferred_source_types",
            "acceptance_criteria",
            "requires_independent_corroboration",
        ):
            if getattr(rule, field_name) is not None:
                updates[field_name] = getattr(rule, field_name)
        if rule.clear_max_age_days:
            updates["max_age_days"] = None
        elif rule.max_age_days is not None:
            updates["max_age_days"] = rule.max_age_days
        if updates:
            state["evidence"] = evidence.model_copy(update=updates)
    if rule.reuse_scope is not None:
        state["reuse_scope"] = rule.reuse_scope


def materialize_profile(
    profile_catalog: ResearchProfileCatalog,
    question_catalog: QuestionCatalog,
    profile_id: str,
    *,
    allow_personal_data: bool = False,
) -> tuple[ResearchProfileSnapshot, list[tuple[str, CatalogQuestion]]]:
    """Resolve inheritance and return an immutable snapshot plus Planner questions."""

    if profile_catalog.question_catalog_version != question_catalog.version:
        raise ProfileCatalogError(
            "Research profile catalog targets question catalog version "
            f"{profile_catalog.question_catalog_version}, got {question_catalog.version}."
        )
    requested_profile = resolve_profile_definition(profile_catalog, profile_id)
    definitions = {profile.profile_id: profile for profile in profile_catalog.profiles}
    catalog_entries = [
        (section.id, question)
        for section in question_catalog.sections
        for question in section.questions
    ]
    entry_by_id = {
        question.id: (section_id, question)
        for section_id, question in catalog_entries
    }
    known_question_ids = set(entry_by_id)

    chain: list[ResearchProfileDefinition] = []
    current: ResearchProfileDefinition | None = requested_profile
    while current is not None:
        chain.append(current)
        current = definitions.get(current.inherits) if current.inherits else None
    chain.reverse()

    states: dict[str, dict[str, Any]] = {}
    for definition in chain:
        unknown_exclusions = set(definition.exclude_question_ids) - known_question_ids
        if unknown_exclusions:
            raise ProfileCatalogError(
                f"Profile {definition.profile_id} excludes unknown questions: "
                f"{sorted(unknown_exclusions)}."
            )
        if definition.reset_catalog_definition_for_included_questions:
            for state in states.values():
                _reset_catalog_definition(state)
        for rule in definition.question_rules:
            entry = entry_by_id.get(rule.question_id)
            if entry is None:
                raise ProfileCatalogError(
                    f"Profile {definition.profile_id} references unknown question "
                    f"{rule.question_id}."
                )
            section_id, question = entry
            if not _question_is_applicable(
                question,
                country=definition.country,
                allow_personal_data=allow_personal_data,
            ):
                continue
            state = states.setdefault(
                question.id, _base_state(question, section_id)
            )
            _apply_rule(
                state,
                rule,
                profile_default_availability=definition.default_availability,
            )
        # Add the remaining legacy-depth fields after explicit rules so a child
        # can classify newly introduced private/manual fields without changing
        # inherited public policies for the same question.
        if definition.include_depth is not None:
            requested_depth = DEPTH_ORDER[definition.include_depth]
            for section_id, question in catalog_entries:
                if (
                    DEPTH_ORDER[question.minimum_depth] <= requested_depth
                    and _question_is_applicable(
                        question,
                        country=definition.country,
                        allow_personal_data=allow_personal_data,
                    )
                ):
                    state = states.setdefault(
                        question.id, _base_state(question, section_id)
                    )
                    for target_field in question.target_fields:
                        if target_field not in state["excluded_fields"]:
                            state["fields"].setdefault(
                                target_field, definition.default_availability
                            )
        for excluded_id in definition.exclude_question_ids:
            states.pop(excluded_id, None)

    empty_questions = [
        question_id for question_id, state in states.items() if not state["fields"]
    ]
    if empty_questions:
        raise ProfileCatalogError(
            f"Profile questions have no selected fields: {sorted(empty_questions)}."
        )

    selected_ids = set(states)
    missing_dependencies = {
        question_id: sorted(set(state["dependencies"]) - selected_ids)
        for question_id, state in states.items()
        if set(state["dependencies"]) - selected_ids
    }
    if missing_dependencies:
        raise ProfileCatalogError(
            "Profile questions have unavailable dependencies: "
            f"{missing_dependencies}."
        )

    selected: list[tuple[str, CatalogQuestion]] = []
    snapshot_questions: list[ResearchProfileQuestionSnapshot] = []
    for section_id, base in catalog_entries:
        state = states.get(base.id)
        if state is None:
            continue
        evidence: EvidenceRule = state["evidence"]
        question = base.model_copy(
            update={
                "title": state["title"],
                "question": state["question"],
                "requirement": state["requirement"],
                "target_fields": list(state["fields"]),
                "evidence": evidence,
                "search_query_templates": state["search_query_templates"],
                "dependencies": state["dependencies"],
            }
        )
        # Re-validate model_copy updates because Pydantic does not validate them.
        question = CatalogQuestion.model_validate(question.model_dump(mode="json"))
        selected.append((section_id, question))
        snapshot_question = ResearchProfileQuestionSnapshot(
            question_id=question.id,
            section_id=section_id,
            title=question.title,
            question=question.question,
            fdd_items=question.fdd_items,
            requirement=question.requirement,
            fields=[
                ResearchProfileFieldPolicy(
                    target_field=field_name,
                    availability=availability,
                    required_for_completion=(
                        availability
                        in requested_profile.completion_required_availabilities
                    ),
                )
                for field_name, availability in state["fields"].items()
            ],
            min_sources=evidence.min_sources,
            preferred_source_types=evidence.preferred_source_types,
            acceptance_criteria=evidence.acceptance_criteria,
            requires_independent_corroboration=(
                evidence.requires_independent_corroboration
            ),
            max_age_days=evidence.max_age_days,
            search_query_templates=question.search_query_templates,
            dependencies=question.dependencies,
            sensitivity=question.sensitivity,
            reuse_scope=state["reuse_scope"],
        )
        snapshot_questions.append(snapshot_question)
    snapshot_payload = dict(
        profile_catalog_version=profile_catalog.version,
        profile_id=requested_profile.profile_id,
        question_catalog_version=question_catalog.version,
        country=requested_profile.country,
        level=requested_profile.level,
        name=requested_profile.name,
        description=requested_profile.description,
        intended_use=requested_profile.intended_use,
        access_scope=requested_profile.access_scope,
        legacy_depth=requested_profile.legacy_depth,
        country_authoritative_sources=list(
            dict.fromkeys(
                source
                for definition in chain
                for source in definition.country_authoritative_sources
            )
        ),
        completion_required_availabilities=(
            requested_profile.completion_required_availabilities
        ),
        questions=snapshot_questions,
    )
    hash_payload = ResearchProfileSnapshot.model_construct(
        profile_sha256="0" * 64,
        **snapshot_payload,
    ).model_dump(mode="json")
    hash_payload.pop("profile_sha256")
    profile_sha256 = hashlib.sha256(
        json.dumps(
            hash_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    snapshot = ResearchProfileSnapshot(
        profile_sha256=profile_sha256,
        **snapshot_payload,
    )
    return snapshot, selected


def available_profiles(
    profile_catalog: ResearchProfileCatalog, *, country: str | None = None
) -> list[ResearchProfileDefinition]:
    normalized_country = country.upper() if country else None
    return [
        profile
        for profile in profile_catalog.profiles
        if normalized_country is None or profile.country == normalized_country
    ]
