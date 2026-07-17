"""Loading and filtering of the canonical franchise question bank."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .schemas import (
    DEPTH_ORDER,
    CatalogQuestion,
    Jurisdiction,
    PlannerInput,
    QuestionCatalog,
)


DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parent / "catalogs" / "franchise_research_v1.yaml"
)
US_FRANCHISE_RULE_JURISDICTIONS = {"US", "PR", "GU", "VI", "AS", "MP", "UM"}


class CatalogError(ValueError):
    """Raised when the canonical question catalog cannot be loaded."""


def load_question_catalog(path: Path | str = DEFAULT_CATALOG_PATH) -> QuestionCatalog:
    catalog_path = Path(path)
    try:
        raw_data = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError(f"Cannot load question catalog: {catalog_path}") from exc

    try:
        return QuestionCatalog.model_validate(raw_data)
    except ValidationError as exc:
        raise CatalogError(f"Question catalog is invalid: {catalog_path}") from exc


def select_questions(
    catalog: QuestionCatalog, planner_input: PlannerInput
) -> list[tuple[str, CatalogQuestion]]:
    selected: list[tuple[str, CatalogQuestion]] = []
    requested_depth = DEPTH_ORDER[planner_input.depth]
    is_us_rule_jurisdiction = (
        planner_input.target_country in US_FRANCHISE_RULE_JURISDICTIONS
    )

    for section in catalog.sections:
        for question in section.questions:
            if DEPTH_ORDER[question.minimum_depth] > requested_depth:
                continue
            if (
                question.jurisdiction == Jurisdiction.US_ONLY
                and not is_us_rule_jurisdiction
            ):
                continue
            if (
                question.sensitivity.value == "personal_data"
                and not planner_input.allow_personal_data
            ):
                continue
            selected.append((section.id, question))
    return selected
