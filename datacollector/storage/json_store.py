"""Filesystem storage for versioned research plans."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from ..schemas import ResearchPlan


DEFAULT_RUNS_DIR = Path(__file__).resolve().parent.parent / "data" / "runs"


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "franchise"


def save_research_plan(
    plan: ResearchPlan, output_dir: Path | str = DEFAULT_RUNS_DIR
) -> Path:
    timestamp = plan.created_at.strftime("%Y%m%dT%H%M%SZ")
    run_directory = (
        Path(output_dir)
        / slugify(plan.planner_input.brand_name)
        / f"{timestamp}_{plan.run_id[:8]}"
    )
    run_directory.mkdir(parents=True, exist_ok=False)
    plan_path = run_directory / "plan.json"
    plan_path.write_text(
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return plan_path
