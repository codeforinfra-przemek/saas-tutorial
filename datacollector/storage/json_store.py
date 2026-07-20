"""Filesystem storage for immutable, versioned research artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..schemas import (
    AgentFailureArtifact,
    CheckerResults,
    ExecutorResults,
    ExtractionResults,
    NormalizerResults,
    ResearchPlan,
    ResolverResults,
    SearchResults,
)


DEFAULT_RUNS_DIR = Path(__file__).resolve().parent.parent / "data" / "runs"


def _fsync_directory(directory: Path) -> None:
    """Durably persist a directory entry created by an atomic publish."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_immutable_text(path: Path, rendered: str) -> None:
    """Publish complete text atomically without replacing an existing artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        output = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with output:
            output.write(rendered)
            output.flush()
            os.fsync(output.fileno())

        # A same-filesystem hard link is an atomic no-replace publish: it either
        # creates the final name for the complete, fsynced inode or raises
        # FileExistsError while leaving the existing artifact untouched.
        os.link(temporary_path, path)
        temporary_path.unlink()
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


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
    _write_immutable_text(
        plan_path,
        json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
    )
    return plan_path


def load_research_plan(path: Path | str) -> tuple[ResearchPlan, str]:
    """Load a plan and return the exact input-byte SHA-256 lineage hash."""

    plan_path = Path(path)
    raw_plan = plan_path.read_bytes()
    plan = ResearchPlan.model_validate_json(raw_plan)
    return plan, hashlib.sha256(raw_plan).hexdigest()


def load_search_results(path: Path | str) -> tuple[SearchResults, str]:
    """Load Searcher output and return its exact input-byte SHA-256."""

    search_path = Path(path)
    raw_search = search_path.read_bytes()
    results = SearchResults.model_validate_json(raw_search)
    return results, hashlib.sha256(raw_search).hexdigest()


def search_results_filename_for(iteration: int, *, offline: bool) -> str:
    stem = "sources" if iteration == 1 else f"sources-r{iteration:03d}"
    if offline:
        stem = f"{stem}-free"
    return f"{stem}.json"


def search_results_filename(results: SearchResults) -> str:
    return search_results_filename_for(
        results.iteration,
        offline=(
            results.generated_by == "offline"
            or (
                results.generated_by == "executor"
                and results.execution_mode == "free"
            )
        ),
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
    _write_immutable_text(result_path, rendered)
    return result_path


def extraction_results_filename_for(iteration: int, *, free: bool) -> str:
    stem = "extractions" if iteration == 1 else f"extractions-r{iteration:03d}"
    if free:
        stem = f"{stem}-free"
    return f"{stem}.json"


def reconciled_extraction_results_filename_for(iteration: int) -> str:
    return f"extractions-r{iteration:03d}-reconciled.json"


def extraction_results_filename(results: ExtractionResults) -> str:
    return extraction_results_filename_for(
        results.iteration,
        free=(
            results.generated_by == "deterministic"
            or (
                results.generated_by == "executor"
                and results.execution_mode == "free"
            )
        ),
    )


def load_extraction_results(
    path: Path | str,
) -> tuple[ExtractionResults, str]:
    extraction_path = Path(path)
    raw_extraction = extraction_path.read_bytes()
    results = ExtractionResults.model_validate_json(raw_extraction)
    reference_root = extraction_path.parent.resolve()
    for document in results.documents:
        if document.content_path is None:
            continue
        content_path = (reference_root / document.content_path).resolve()
        if not content_path.is_relative_to(reference_root):
            raise ValueError(
                f"Raw-document path escapes result directory: {document.content_path}"
            )
        try:
            content = content_path.read_bytes()
        except OSError as exc:
            raise ValueError(
                f"Raw-document snapshot is unavailable: {document.content_path}"
            ) from exc
        if document.content_bytes is not None and len(content) != document.content_bytes:
            raise ValueError(
                f"Raw-document byte count mismatch: {document.content_path}"
            )
        if (
            document.content_sha256 is None
            or hashlib.sha256(content).hexdigest() != document.content_sha256
        ):
            raise ValueError(
                f"Raw-document SHA-256 mismatch: {document.content_path}"
            )
    return results, hashlib.sha256(raw_extraction).hexdigest()


def save_extraction_results(
    results: ExtractionResults,
    search_path: Path | str,
    output_dir: Path | str | None = None,
) -> Path:
    """Save beside explicit Searcher output and never silently overwrite."""

    directory = Path(output_dir) if output_dir is not None else Path(search_path).parent
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / extraction_results_filename(results)
    rendered = (
        json.dumps(
            results.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    _write_immutable_text(result_path, rendered)
    return result_path


def save_reconciled_extraction_results(
    results: ExtractionResults,
    current_extraction_path: Path | str,
    output_dir: Path | str | None = None,
) -> Path:
    """Save an offline repair beside its source without overwriting history."""

    if results.reconciled_from_extraction_id is None:
        raise ValueError("Reconciled extraction is missing repair lineage.")
    directory = (
        Path(output_dir)
        if output_dir is not None
        else Path(current_extraction_path).parent
    )
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / reconciled_extraction_results_filename_for(
        results.iteration
    )
    rendered = (
        json.dumps(
            results.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    _write_immutable_text(result_path, rendered)
    return result_path


def checker_results_filename_for(iteration: int, *, free: bool) -> str:
    stem = "check" if iteration == 1 else f"check-r{iteration:03d}"
    if free:
        stem = f"{stem}-free"
    return f"{stem}.json"


def checker_results_filename(results: CheckerResults) -> str:
    return checker_results_filename_for(
        results.iteration,
        free=results.generated_by == "deterministic",
    )


def load_checker_results(
    path: Path | str,
) -> tuple[CheckerResults, str]:
    """Load Checker output and return its exact input-byte SHA-256."""

    checker_path = Path(path)
    raw_checker = checker_path.read_bytes()
    results = CheckerResults.model_validate_json(raw_checker)
    return results, hashlib.sha256(raw_checker).hexdigest()


def save_checker_results(
    results: CheckerResults,
    extraction_path: Path | str,
    output_dir: Path | str | None = None,
) -> Path:
    """Save beside explicit Extractor output and never silently overwrite."""

    directory = (
        Path(output_dir) if output_dir is not None else Path(extraction_path).parent
    )
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / checker_results_filename(results)
    rendered = (
        json.dumps(
            results.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    _write_immutable_text(result_path, rendered)
    return result_path


def resolver_results_filename_for(iteration: int, *, free: bool) -> str:
    stem = "resolution" if iteration == 1 else f"resolution-r{iteration:03d}"
    if free:
        stem = f"{stem}-free"
    return f"{stem}.json"


def resolver_results_filename(results: ResolverResults) -> str:
    return resolver_results_filename_for(
        results.iteration,
        free=results.generated_by == "deterministic",
    )


def load_resolver_results(
    path: Path | str,
) -> tuple[ResolverResults, str]:
    """Load Resolver output and return its exact input-byte SHA-256."""

    resolver_path = Path(path)
    raw_resolver = resolver_path.read_bytes()
    results = ResolverResults.model_validate_json(raw_resolver)
    return results, hashlib.sha256(raw_resolver).hexdigest()


def save_resolver_results(
    results: ResolverResults,
    checker_path: Path | str,
    output_dir: Path | str | None = None,
) -> Path:
    """Save beside the explicit Checker output and never silently overwrite."""

    directory = (
        Path(output_dir) if output_dir is not None else Path(checker_path).parent
    )
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / resolver_results_filename(results)
    rendered = (
        json.dumps(
            results.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    _write_immutable_text(result_path, rendered)
    return result_path


def executor_results_filename_for(iteration: int, *, free: bool) -> str:
    stem = "execution" if iteration == 1 else f"execution-r{iteration:03d}"
    if free:
        stem = f"{stem}-free"
    return f"{stem}.json"


def executor_results_filename(results: ExecutorResults) -> str:
    return executor_results_filename_for(
        results.iteration,
        free=results.execution_mode.value == "free",
    )


def load_executor_results(
    path: Path | str,
) -> tuple[ExecutorResults, str]:
    """Load Executor manifest and return its exact input-byte SHA-256."""

    executor_path = Path(path)
    raw_executor = executor_path.read_bytes()
    results = ExecutorResults.model_validate_json(raw_executor)
    return results, hashlib.sha256(raw_executor).hexdigest()


def save_executor_results(
    results: ExecutorResults,
    resolution_path: Path | str,
    output_dir: Path | str | None = None,
) -> Path:
    """Save beside the explicit Resolver output and never overwrite."""

    directory = (
        Path(output_dir) if output_dir is not None else Path(resolution_path).parent
    )
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / executor_results_filename(results)
    rendered = (
        json.dumps(
            results.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    _write_immutable_text(result_path, rendered)
    return result_path


def normalizer_results_filename_for(iteration: int, *, free: bool) -> str:
    stem = "normalized" if iteration == 1 else f"normalized-r{iteration:03d}"
    if free:
        stem = f"{stem}-free"
    return f"{stem}.json"


def normalizer_results_filename(results: NormalizerResults) -> str:
    return normalizer_results_filename_for(
        results.iteration,
        free=results.normalization_mode.value == "free",
    )


def load_normalizer_results(
    path: Path | str,
) -> tuple[NormalizerResults, str]:
    """Load Normalizer output and return its exact input-byte SHA-256."""

    normalizer_path = Path(path)
    raw_normalizer = normalizer_path.read_bytes()
    results = NormalizerResults.model_validate_json(raw_normalizer)
    return results, hashlib.sha256(raw_normalizer).hexdigest()


def save_normalizer_results(
    results: NormalizerResults,
    checker_path: Path | str,
    output_dir: Path | str | None = None,
) -> Path:
    """Save beside the exact Checker input and never overwrite."""

    directory = (
        Path(output_dir) if output_dir is not None else Path(checker_path).parent
    )
    directory.mkdir(parents=True, exist_ok=True)
    result_path = directory / normalizer_results_filename(results)
    rendered = (
        json.dumps(
            results.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    _write_immutable_text(result_path, rendered)
    return result_path


@contextmanager
def reserve_artifact(path: Path | str) -> Iterator[Path]:
    """Prevent cooperating processes from paying for the same output concurrently."""

    artifact_path = Path(path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = artifact_path.with_name(f".{artifact_path.name}.lock")
    if artifact_path.exists():
        raise FileExistsError(
            f"Artifact already exists and will not be overwritten: "
            f"{artifact_path}"
        )
    try:
        with lock_path.open("x", encoding="utf-8") as lock:
            lock.write(f"reserved_for={artifact_path.name}\n")
    except FileExistsError:
        raise FileExistsError(
            f"Artifact is already reserved by another process: {artifact_path}"
        ) from None
    try:
        if artifact_path.exists():
            raise FileExistsError(
                f"Artifact already exists and will not be overwritten: "
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
    _write_immutable_text(failure_path, rendered)
    return failure_path
