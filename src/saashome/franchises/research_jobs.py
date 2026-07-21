"""Database-backed execution queue for paid datacollector stages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone


REPOSITORY_ROOT = settings.BASE_DIR.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from datacollector.storage.json_store import (  # noqa: E402
    load_checker_results,
    load_loop_results,
    load_normalizer_results,
)

from .models import (  # noqa: E402
    FranchiseResearchEvent,
    FranchiseResearchJob,
    FranchiseResearchWorkspace,
)
from .research_workbench import create_research_workspace  # noqa: E402
from .research_finalizer import finalize_research_workspace  # noqa: E402


class ResearchJobError(ValueError):
    """A safe user-facing execution or lineage error."""


@dataclass(frozen=True)
class ResearchCommandResult:
    returncode: int
    stdout: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _current_check(workspace: FranchiseResearchWorkspace) -> tuple[Path, str]:
    latest = (
        workspace.jobs.filter(
            status=FranchiseResearchJob.STATUS_SUCCEEDED,
        )
        .exclude(result_check_reference="")
        .order_by("-completed_at", "-id")
        .first()
    )
    if latest:
        path = Path(latest.result_check_reference).resolve()
        expected_sha256 = latest.result_check_sha256
        if not expected_sha256:
            raise ResearchJobError("The previous job did not retain its Checker hash.")
    else:
        normalized_path = Path(workspace.normalized_reference).resolve()
        normalized, normalized_sha256 = load_normalizer_results(normalized_path)
        if normalized_sha256 != workspace.normalized_sha256:
            raise ResearchJobError("Normalizer bytes no longer match this Workbench.")
        path = Path(normalized.check_reference).resolve()
        expected_sha256 = normalized.check_sha256
    checker, actual_sha256 = load_checker_results(path)
    if actual_sha256 != expected_sha256 or checker.plan_run_id != str(workspace.plan_run_id):
        raise ResearchJobError("The current Checker artifact has inconsistent lineage.")
    return path, actual_sha256


@transaction.atomic
def queue_research_job(
    workspace: FranchiseResearchWorkspace,
    *,
    kind: str,
    configuration: dict,
    requested_by=None,
) -> FranchiseResearchJob:
    if kind not in {choice[0] for choice in FranchiseResearchJob.KIND_CHOICES}:
        raise ResearchJobError("Unknown research job kind.")
    if workspace.jobs.filter(
        status__in=[
            FranchiseResearchJob.STATUS_QUEUED,
            FranchiseResearchJob.STATUS_RUNNING,
        ]
    ).exists():
        raise ResearchJobError("This Workbench already has an active job.")
    if kind == FranchiseResearchJob.KIND_FINALIZE:
        input_path = Path(workspace.normalized_reference).resolve()
        input_sha256 = workspace.normalized_sha256
        if workspace.status not in {
            FranchiseResearchWorkspace.STATUS_APPROVED,
            FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS,
        }:
            raise ResearchJobError("First approve the Workbench before finalization.")
        checker = None
    else:
        input_path, input_sha256 = _current_check(workspace)
        checker, _ = load_checker_results(input_path)
    if (
        kind == FranchiseResearchJob.KIND_NORMALIZE
        and checker is not None
        and not checker.passed
        and not configuration.get("normalize_incomplete")
    ):
        raise ResearchJobError(
            "The Checker did not pass. Explicitly allow an incomplete draft first."
        )
    try:
        job = FranchiseResearchJob.objects.create(
            workspace=workspace,
            kind=kind,
            input_reference=str(input_path),
            input_sha256=input_sha256,
            configuration=configuration,
            requested_by=requested_by,
        )
    except IntegrityError as exc:
        raise ResearchJobError("This Workbench already has an active job.") from exc
    FranchiseResearchEvent.objects.create(
        workspace=workspace,
        event_type="job_queued",
        message=f"Dodano do kolejki: {job.get_kind_display()}.",
        metadata={"job_id": str(job.job_id), "kind": job.kind},
        actor=requested_by,
    )
    return job


def cancel_queued_job(job: FranchiseResearchJob, *, actor=None) -> None:
    if job.status != FranchiseResearchJob.STATUS_QUEUED:
        raise ResearchJobError("Only a queued job can be cancelled.")
    job.status = FranchiseResearchJob.STATUS_CANCELLED
    job.current_stage = "Anulowane przed uruchomieniem"
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "current_stage", "completed_at"])
    FranchiseResearchEvent.objects.create(
        workspace=job.workspace,
        event_type="job_cancelled",
        message=f"Anulowano zadanie: {job.get_kind_display()}.",
        metadata={"job_id": str(job.job_id)},
        actor=actor,
    )


def claim_next_job() -> FranchiseResearchJob | None:
    with transaction.atomic():
        job = (
            FranchiseResearchJob.objects.select_for_update()
            .filter(status=FranchiseResearchJob.STATUS_QUEUED)
            .order_by("queued_at", "id")
            .first()
        )
        if job is None:
            return None
        now = timezone.now()
        job.status = FranchiseResearchJob.STATUS_RUNNING
        job.current_stage = "Walidacja wejścia"
        job.progress_percent = 5
        job.started_at = now
        job.heartbeat_at = now
        job.save(
            update_fields=[
                "status",
                "current_stage",
                "progress_percent",
                "started_at",
                "heartbeat_at",
            ]
        )
        return job


_ITERATION = re.compile(r"-r(\d{3,})")


def _next_iteration(directory: Path, minimum: int) -> int:
    observed = [
        int(match.group(1))
        for path in directory.glob("*.json")
        if (match := _ITERATION.search(path.name))
    ]
    return max([minimum, *observed], default=minimum) + 1


def build_research_command(job: FranchiseResearchJob) -> list[str]:
    input_path = Path(job.input_reference).resolve()
    if _sha256(input_path) != job.input_sha256:
        raise ResearchJobError("Job input changed after it was queued.")
    checker, checker_sha256 = load_checker_results(input_path)
    if checker_sha256 != job.input_sha256:
        raise ResearchJobError("Job input is not the expected Checker artifact.")
    if checker.plan_run_id != str(job.workspace.plan_run_id):
        raise ResearchJobError("Job Checker belongs to another research plan.")
    config = job.configuration
    python = sys.executable
    if job.kind == FranchiseResearchJob.KIND_LOOP:
        command = [
            python,
            "-m",
            "datacollector",
            "loop",
            "--check",
            str(input_path),
            "--max-rounds",
            str(config["max_rounds"]),
            "--max-cost-usd",
            str(config["max_cost_usd"]),
            "--max-stagnant-rounds",
            "1",
            "--max-follow-ups",
            "30",
            "--max-source-actions",
            "10",
            "--max-search-tasks",
            "5",
            "--max-queries-per-item",
            "3",
            "--max-search-calls",
            str(config["max_search_calls"]),
            "--min-queries-per-task",
            "2",
            "--max-candidate-routes",
            "3",
            "--max-extractor-api-calls",
            str(config["max_extractor_api_calls"]),
            "--max-checker-claims",
            "500",
            "--max-checker-evidence-chars",
            "500000",
        ]
        command.append(
            "--advance-with-documented-gaps"
            if config["policy"] == "advance"
            else "--allow-plan-repair-limit"
        )
        if config.get("normalize_incomplete"):
            command.append("--normalize-incomplete")
        else:
            command.append("--skip-normalize")
        return command
    iteration = _next_iteration(input_path.parent, checker.iteration)
    if job.kind == FranchiseResearchJob.KIND_CHECK:
        return [
            python,
            "-m",
            "datacollector",
            "check",
            "--extractions",
            str(Path(checker.extraction_reference).resolve()),
            "--iteration",
            str(iteration),
            "--max-claims",
            "500",
            "--max-evidence-chars",
            "500000",
        ]
    command = [
        python,
        "-m",
        "datacollector",
        "normalize",
        "--check",
        str(input_path),
        "--iteration",
        str(iteration),
        "--max-claims",
        "500",
        "--max-input-chars",
        "500000",
    ]
    if not checker.passed:
        command.append("--allow-incomplete")
    return command


def _stage_from_new_artifacts(directory: Path, since_timestamp: float) -> tuple[str, int]:
    observed = {
        path.name
        for path in directory.glob("*.json")
        if path.stat().st_mtime >= since_timestamp
    }
    if any(name.startswith("normalized-r") for name in observed):
        return "Normalizer / przygotowanie draftu", 92
    if any(name.startswith("check-r") for name in observed):
        return "Finalizacja kontroli jakości", 85
    if any(name.startswith("execution-r") for name in observed):
        return "Checker / kontrola nowych danych", 72
    if any(name.startswith("extractions-r") for name in observed):
        return "Extractor / łączenie dowodów", 62
    if any(name.startswith("sources-r") for name in observed):
        return "Extractor / analiza źródeł", 48
    if any(name.startswith("resolution-r") for name in observed):
        return "Executor / wyszukiwanie i ekstrakcja", 30
    return "Resolver / przygotowanie następnego kroku", 15


def _append_log(job: FranchiseResearchJob, text: str) -> None:
    if not text:
        return
    job.log = (job.log + text)[-100_000:]
    job.heartbeat_at = timezone.now()
    job.save(update_fields=["log", "heartbeat_at"])


def _run_monitored_command(job: FranchiseResearchJob, command: list[str]) -> ResearchCommandResult:
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    started_timestamp = time.time()
    timeout_seconds = int(getattr(settings, "RESEARCH_JOB_TIMEOUT_SECONDS", 3600))
    process = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    captured: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None:
        if time.monotonic() >= deadline:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            raise ResearchJobError(
                f"Research job exceeded its {timeout_seconds}-second time limit."
            )
        ready, _, _ = select.select([process.stdout], [], [], 1.0)
        if ready:
            line = process.stdout.readline()
            if line:
                captured.append(line)
                _append_log(job, line)
        stage, progress = _stage_from_new_artifacts(
            Path(job.input_reference).parent,
            started_timestamp,
        )
        if stage != job.current_stage or progress != job.progress_percent:
            job.current_stage = stage
            job.progress_percent = progress
            job.heartbeat_at = timezone.now()
            job.save(
                update_fields=["current_stage", "progress_percent", "heartbeat_at"]
            )
    remainder = process.stdout.read() if process.stdout else ""
    if remainder:
        captured.append(remainder)
        _append_log(job, remainder)
    return ResearchCommandResult(process.returncode, "".join(captured))


def _parse_summary(output: str) -> dict:
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ResearchJobError("Datacollector returned no machine-readable summary.")


def _cost_summary(summary: dict) -> dict:
    usage = summary.get("usage_totals") or {}
    return {
        "api_attempts_recorded": usage.get("api_attempts_recorded", 0),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "reasoning_tokens": usage.get("reasoning_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "tool_calls": usage.get("tool_calls", 0),
        "tool_cost_usd": usage.get("tool_cost_usd", "0"),
        "estimated_cost_usd": usage.get("estimated_cost_usd"),
    }


def _apply_success(job: FranchiseResearchJob, summary: dict) -> None:
    normalized_reference = ""
    if job.kind == FranchiseResearchJob.KIND_LOOP:
        loop_path = Path(summary["loop_path"]).resolve()
        loop, loop_sha256 = load_loop_results(loop_path)
        if loop.initial_check_sha256 != job.input_sha256:
            raise ResearchJobError("Loop output does not descend from the queued input.")
        job.result_loop_reference = str(loop_path)
        job.result_loop_sha256 = loop_sha256
        job.result_check_reference = str(Path(loop.final_check_reference).resolve())
        final_checker, final_check_sha256 = load_checker_results(
            job.result_check_reference
        )
        if (
            final_check_sha256 != loop.final_check_sha256
            or final_checker.check_id != loop.final_check_id
        ):
            raise ResearchJobError("Loop final Checker bytes are inconsistent.")
        job.result_check_sha256 = final_check_sha256
        normalized_reference = loop.normalization_reference or ""
        cost = {
            "api_attempts_recorded": loop.incremental_api_attempts,
            "input_tokens": loop.incremental_input_tokens,
            "output_tokens": loop.incremental_output_tokens,
            "reasoning_tokens": loop.incremental_reasoning_tokens,
            "total_tokens": loop.incremental_total_tokens,
            "tool_calls": loop.incremental_tool_calls,
            "tool_cost_usd": str(loop.incremental_tool_cost_usd),
            "estimated_cost_usd": (
                str(loop.incremental_estimated_cost_usd)
                if loop.incremental_estimated_cost_usd is not None
                else None
            ),
        }
    elif job.kind == FranchiseResearchJob.KIND_CHECK:
        check_path = Path(summary["check_path"]).resolve()
        checker, check_sha256 = load_checker_results(check_path)
        if checker.plan_run_id != str(job.workspace.plan_run_id):
            raise ResearchJobError("Checker output belongs to another plan.")
        job.result_check_reference = str(check_path)
        job.result_check_sha256 = check_sha256
        cost = _cost_summary(summary)
    else:
        normalized_reference = str(Path(summary["normalized_path"]).resolve())
        normalized, normalized_sha256 = load_normalizer_results(normalized_reference)
        if normalized.plan_run_id != str(job.workspace.plan_run_id):
            raise ResearchJobError("Normalizer output belongs to another plan.")
        job.result_check_reference = job.input_reference
        job.result_check_sha256 = job.input_sha256
        job.result_normalized_sha256 = normalized_sha256
        cost = _cost_summary(summary)
    if normalized_reference:
        result_workspace, _ = create_research_workspace(
            normalized_reference,
            franchise_slug=job.workspace.franchise.slug,
            created_by=job.requested_by,
        )
        job.result_normalized_reference = normalized_reference
        if not job.result_normalized_sha256:
            _, job.result_normalized_sha256 = load_normalizer_results(
                normalized_reference
            )
        job.result_workspace = result_workspace
    job.status = FranchiseResearchJob.STATUS_SUCCEEDED
    job.current_stage = "Zakończone"
    job.progress_percent = 100
    job.result_summary = summary
    job.cost_summary = cost
    job.completed_at = timezone.now()
    job.heartbeat_at = job.completed_at
    job.save()
    FranchiseResearchEvent.objects.create(
        workspace=job.workspace,
        event_type="job_succeeded",
        message=f"Zakończono: {job.get_kind_display()}.",
        metadata={
            "job_id": str(job.job_id),
            "cost": cost.get("estimated_cost_usd"),
            "result_workspace_id": (
                str(job.result_workspace.workspace_id) if job.result_workspace else None
            ),
        },
        actor=job.requested_by,
    )


def process_research_job(
    job: FranchiseResearchJob,
    *,
    runner: Callable[[FranchiseResearchJob, list[str]], ResearchCommandResult] = _run_monitored_command,
) -> FranchiseResearchJob:
    if job.status != FranchiseResearchJob.STATUS_RUNNING:
        raise ResearchJobError("The job must be claimed before execution.")
    try:
        if job.kind == FranchiseResearchJob.KIND_FINALIZE:
            job.current_stage = "Finalizer / walidacja i zamrożenie wersji"
            job.progress_percent = 20
            job.heartbeat_at = timezone.now()
            job.save(
                update_fields=["current_stage", "progress_percent", "heartbeat_at"]
            )
            finalization, created = finalize_research_workspace(
                job.workspace,
                actor=job.requested_by,
                active_job_id=job.job_id,
            )
            job.status = FranchiseResearchJob.STATUS_SUCCEEDED
            job.current_stage = "Opublikowano wersję"
            job.progress_percent = 100
            job.result_summary = {
                "finalization_id": str(finalization.finalization_id),
                "release_number": finalization.release_number,
                "research_import_id": finalization.research_import_id,
                "franchise_slug": finalization.research_import.franchise.slug,
                "artifact_sha256": finalization.artifact_sha256,
                "created": created,
            }
            job.cost_summary = {
                "api_attempts_recorded": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "tool_calls": 0,
                "tool_cost_usd": "0",
                "estimated_cost_usd": "0",
            }
            job.completed_at = timezone.now()
            job.heartbeat_at = job.completed_at
            job.save()
            FranchiseResearchEvent.objects.create(
                workspace=job.workspace,
                event_type="job_succeeded",
                message=f"Opublikowano wersję {finalization.release_number} researchu.",
                metadata={
                    "job_id": str(job.job_id),
                    "finalization_id": str(finalization.finalization_id),
                    "release_number": finalization.release_number,
                },
                actor=job.requested_by,
            )
            return job
        command = build_research_command(job)
        job.current_stage = {
            FranchiseResearchJob.KIND_LOOP: "Resolver / przygotowanie następnego kroku",
            FranchiseResearchJob.KIND_CHECK: "Checker / kontrola jakości",
            FranchiseResearchJob.KIND_NORMALIZE: "Normalizer / przygotowanie draftu",
            FranchiseResearchJob.KIND_FINALIZE: "Finalizer / publikacja",
        }[job.kind]
        job.progress_percent = 10
        job.save(update_fields=["current_stage", "progress_percent"])
        result = runner(job, command)
        if result.returncode:
            raise ResearchJobError(
                f"Datacollector exited with status {result.returncode}."
            )
        summary = _parse_summary(result.stdout)
        _apply_success(job, summary)
    except Exception as exc:
        job.status = FranchiseResearchJob.STATUS_FAILED
        job.current_stage = "Zatrzymane z błędem"
        job.error_code = type(exc).__name__[:80]
        job.error_message = str(exc)[:4000]
        job.completed_at = timezone.now()
        job.heartbeat_at = job.completed_at
        job.save(
            update_fields=[
                "status",
                "current_stage",
                "error_code",
                "error_message",
                "completed_at",
                "heartbeat_at",
            ]
        )
        FranchiseResearchEvent.objects.create(
            workspace=job.workspace,
            event_type="job_failed",
            message=f"Zadanie zatrzymało się: {job.get_kind_display()}.",
            metadata={"job_id": str(job.job_id), "error_code": job.error_code},
            actor=job.requested_by,
        )
    return job


def fail_stale_jobs(*, older_than: timedelta = timedelta(hours=2)) -> int:
    threshold = timezone.now() - older_than
    stale = FranchiseResearchJob.objects.filter(
        status=FranchiseResearchJob.STATUS_RUNNING,
        heartbeat_at__lt=threshold,
    )
    count = 0
    for job in stale:
        job.status = FranchiseResearchJob.STATUS_FAILED
        job.current_stage = "Worker przestał odpowiadać"
        job.error_code = "stale_worker"
        job.error_message = "No worker heartbeat was recorded within the safety window."
        job.completed_at = timezone.now()
        job.save(
            update_fields=[
                "status",
                "current_stage",
                "error_code",
                "error_message",
                "completed_at",
            ]
        )
        count += 1
    return count
