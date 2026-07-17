"""Filesystem storage for immutable, versioned research artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..schemas import AgentFailureArtifact, ResearchPlan, SearchResults


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
    filename = "plan-free.json" if plan.generated_by == "offline" else "plan.json"
    plan_path = run_directory / filename
    plan_path.write_text(
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return plan_path


def load_research_plan(path: Path | str) -> tuple[ResearchPlan, str]:
    """Load a plan and return the exact input-byte SHA-256 lineage hash."""

    plan_path = Path(path)
    raw_plan = plan_path.read_bytes()
    plan = ResearchPlan.model_validate_json(raw_plan)
    return plan, hashlib.sha256(raw_plan).hexdigest()


def search_results_filename_for(iteration: int, *, offline: bool) -> str:
    stem = "sources" if iteration == 1 else f"sources-r{iteration:03d}"
    if offline:
        stem = f"{stem}-free"
    return f"{stem}.json"


def search_results_filename(results: SearchResults) -> str:
    return search_results_filename_for(
        results.iteration,
        offline=results.generated_by == "offline",
    )


def save_search_results(
    results: SearchResults,
    plan_path: Path | str,
    output_dir: Path | str | None = None,
) -> Path:
    """Save beside the explicit plan by default and never silently overwrite."""

    directory = Path(output_dir) if output_dir is not None else Path(plan_path).parent
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / search_results_filename(results)
    rendered = (
        json.dumps(
            results.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    with result_path.open("x", encoding="utf-8") as output:
        output.write(rendered)
    return result_path


@contextmanager
def reserve_artifact(path: Path | str) -> Iterator[Path]:
    """Prevent cooperating processes from paying for the same output concurrently."""

    artifact_path = Path(path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = artifact_path.with_name(f".{artifact_path.name}.lock")
    if artifact_path.exists():
        raise FileExistsError(
            f"Search artifact already exists and will not be overwritten: "
            f"{artifact_path}"
        )
    try:
        with lock_path.open("x", encoding="utf-8") as lock:
            lock.write(f"reserved_for={artifact_path.name}\n")
    except FileExistsError:
        raise FileExistsError(
            f"Search artifact is already reserved by another process: {artifact_path}"
        ) from None
    try:
        if artifact_path.exists():
            raise FileExistsError(
                f"Search artifact already exists and will not be overwritten: "
                f"{artifact_path}"
            )
        yield artifact_path
    finally:
        lock_path.unlink(missing_ok=True)


def save_agent_failure(
    failure: AgentFailureArtifact,
    plan_path: Path | str,
    output_dir: Path | str | None = None,
) -> Path:
    """Persist known usage/cost facts for an unusable provider response."""

    directory = Path(output_dir) if output_dir is not None else Path(plan_path).parent
    attempt_directory = directory / "attempts"
    attempt_directory.mkdir(parents=True, exist_ok=True)
    agent = failure.usage.agent if failure.usage is not None else failure.agent
    iteration = (
        failure.usage.iteration if failure.usage is not None else failure.iteration
    )
    call_index = (
        failure.usage.call_index if failure.usage is not None else failure.call_index
    )
    if agent is None or iteration is None or call_index is None:
        raise ValueError("Failure artifact is missing filename metadata.")
    filename = (
        f"{agent}-r{iteration:03d}-c{call_index:03d}-"
        f"failed-{failure.failure_id[:8]}.json"
    )
    failure_path = attempt_directory / filename
    rendered = (
        json.dumps(
            failure.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    with failure_path.open("x", encoding="utf-8") as output:
        output.write(rendered)
    return failure_path
