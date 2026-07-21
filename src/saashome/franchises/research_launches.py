"""Durable first-run orchestration from a directory entry to a Workbench."""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone


REPOSITORY_ROOT = settings.BASE_DIR.parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from datacollector.storage.json_store import (
    load_checker_results,
    load_extraction_results,
    load_normalizer_results,
    load_research_plan,
    load_search_results,
)

from .models import Franchise, FranchiseResearchLaunch
from .research_jobs import ResearchCommandResult
from .research_workbench import create_research_workspace


PROFILE_CHOICES = frozenset({"PL:L1", "PL:L2", "PL:L3"})
UNKNOWN_PROVIDER_ATTEMPT_RESERVE_USD = Decimal("0.50")


class ResearchLaunchError(ValueError):
    """Safe orchestration or immutable-lineage error."""


_TRANSIENT_PROVIDER_FAILURES = {
    "InternalServerError": "provider_server_error",
    "APITimeoutError": "timeout",
    "RateLimitError": "rate_limit",
    "APIConnectionError": "connection_error",
    "ServiceUnavailableError": "provider_server_error",
}
_PROVIDER_FAILURE_TYPE = re.compile(r"request failed \(([A-Za-z0-9_]+)\)")


def _transient_provider_failure(output: str) -> str | None:
    """Recognize only failures that are safe to retry as a fresh request."""

    match = _PROVIDER_FAILURE_TYPE.search(output or "")
    if match:
        return _TRANSIENT_PROVIDER_FAILURES.get(match.group(1))
    lowered = (output or "").lower()
    for code in ("provider_server_error", "timeout", "rate_limit", "connection_error"):
        if f"error_code\": \"{code}" in lowered or f"with {code}" in lowered:
            return code
    return None


def _unknown_provider_attempt_summary(error_code: str) -> dict:
    """Reserve cost when a failed provider request returned no usage metadata."""

    return {
        "agent_usage": [],
        "failed_attempts": [
            {
                "error_code": error_code,
                "usage_recorded": False,
                "token_usage_unknown": True,
            }
        ],
    }


def _provider_failure_summaries(launch: FranchiseResearchLaunch) -> list[dict]:
    return [
        _unknown_provider_attempt_summary(item.get("error_code") or "provider_exception")
        for item in (launch.provider_failure_history or [])
        if item.get("token_usage_unknown", True)
    ]


def _record_unknown_provider_failure(
    launch: FranchiseResearchLaunch,
    *,
    error_code: str,
    stage: str,
) -> None:
    history = list(launch.provider_failure_history or [])
    history.append(
        {
            "error_code": error_code,
            "stage": stage,
            "usage_recorded": False,
            "token_usage_unknown": True,
            "recorded_at": timezone.now().isoformat(),
        }
    )
    launch.provider_failure_history = history[-50:]
    launch.save(update_fields=["provider_failure_history"])


def _apply_failure_history_reserve(launch: FranchiseResearchLaunch) -> None:
    unknown_attempts = sum(
        1
        for item in (launch.provider_failure_history or [])
        if item.get("token_usage_unknown", True)
    )
    summary = dict(launch.cost_summary or {})
    already_recorded = int(summary.get("unknown_cost_attempts") or 0)
    missing_attempts = max(unknown_attempts - already_recorded, 0)
    if not missing_attempts:
        return
    reserve = UNKNOWN_PROVIDER_ATTEMPT_RESERVE_USD * unknown_attempts
    known_cost = _decimal(summary.get("estimated_cost_usd")) or Decimal("0")
    summary["api_attempts_recorded"] = int(
        summary.get("api_attempts_recorded") or 0
    ) + missing_attempts
    summary["unknown_cost_attempts"] = unknown_attempts
    summary["unknown_cost_reserve_usd"] = str(reserve)
    summary["budgeted_cost_usd"] = str(known_cost + reserve)
    summary["cost_complete"] = False
    launch.cost_summary = summary
    launch.save(update_fields=["cost_summary"])


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
    raise ResearchLaunchError("Etap nie zwrócił podsumowania maszynowego.")


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _usage(summary: dict) -> dict:
    totals = summary.get("usage_totals")
    records = summary.get("agent_usage") or []
    failed_attempts = summary.get("failed_attempts") or []
    unknown_attempts = sum(
        1 for attempt in failed_attempts if attempt.get("token_usage_unknown")
    )

    def record_value(record, name):
        return record.get(name) or (record.get("tokens") or {}).get(name) or 0

    def record_cost(record):
        return _decimal(record.get("estimated_cost_usd")) or _decimal(
            (record.get("cost_estimate") or {}).get("total_estimated_cost_usd")
        )

    known_record_costs = [record_cost(record) for record in records]
    known_cost = sum(
        (item for item in known_record_costs if item is not None), Decimal("0")
    )
    record_costs_complete = all(item is not None for item in known_record_costs)
    if totals is None:
        return {
            "api_attempts_recorded": len(records) + len(failed_attempts),
            "input_tokens": sum(int(record_value(item, "input_tokens")) for item in records),
            "output_tokens": sum(int(record_value(item, "output_tokens")) for item in records),
            "reasoning_tokens": sum(int(record_value(item, "reasoning_tokens")) for item in records),
            "total_tokens": sum(int(record_value(item, "total_tokens")) for item in records),
            "tool_calls": sum(
                int(item.get("tool_calls") or sum(
                    int(tool.get("calls") or 0) for tool in item.get("tool_usage") or []
                ))
                for item in records
            ),
            "tool_cost_usd": sum(
                (
                    _decimal(item.get("tool_cost_usd"))
                    or _decimal((item.get("cost_estimate") or {}).get("tool_cost_usd"))
                    or Decimal("0")
                    for item in records
                ),
                Decimal("0"),
            ),
            "estimated_cost_usd": known_cost,
            "cost_complete": record_costs_complete and unknown_attempts == 0,
            "unknown_cost_attempts": unknown_attempts,
        }
    total_cost = _decimal(totals.get("estimated_cost_usd"))
    return {
        "api_attempts_recorded": int(totals.get("api_attempts_recorded") or 0),
        "input_tokens": int(totals.get("input_tokens") or 0),
        "output_tokens": int(totals.get("output_tokens") or 0),
        "reasoning_tokens": int(totals.get("reasoning_tokens") or 0),
        "total_tokens": int(totals.get("total_tokens") or 0),
        "tool_calls": int(totals.get("tool_calls") or 0),
        "tool_cost_usd": _decimal(totals.get("tool_cost_usd")) or Decimal("0"),
        "estimated_cost_usd": total_cost if total_cost is not None else known_cost,
        "cost_complete": total_cost is not None and unknown_attempts == 0,
        "unknown_cost_attempts": unknown_attempts,
    }


def _combined_usage(stage_summaries: list[dict]) -> dict:
    records = [_usage(summary) for summary in stage_summaries]
    costs = [item["estimated_cost_usd"] for item in records]
    estimated = sum(costs, Decimal("0"))
    unknown_attempts = sum(item["unknown_cost_attempts"] for item in records)
    reserve = UNKNOWN_PROVIDER_ATTEMPT_RESERVE_USD * unknown_attempts
    return {
        "api_attempts_recorded": sum(item["api_attempts_recorded"] for item in records),
        "input_tokens": sum(item["input_tokens"] for item in records),
        "output_tokens": sum(item["output_tokens"] for item in records),
        "reasoning_tokens": sum(item["reasoning_tokens"] for item in records),
        "total_tokens": sum(item["total_tokens"] for item in records),
        "tool_calls": sum(item["tool_calls"] for item in records),
        "tool_cost_usd": str(sum((item["tool_cost_usd"] for item in records), Decimal("0"))),
        "estimated_cost_usd": str(estimated),
        "cost_complete": all(item["cost_complete"] for item in records),
        "unknown_cost_attempts": unknown_attempts,
        "unknown_cost_reserve_usd": str(reserve),
        "budgeted_cost_usd": str(estimated + reserve),
    }


def _default_runner(launch: FranchiseResearchLaunch, command: list[str]) -> ResearchCommandResult:
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
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
    last_heartbeat = time.monotonic()
    while process.poll() is None:
        if time.monotonic() >= deadline:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
            raise ResearchLaunchError(
                f"Etap przekroczył limit {timeout_seconds} sekund."
            )
        ready, _, _ = select.select([process.stdout], [], [], 1.0)
        if ready:
            line = process.stdout.readline()
            if line:
                captured.append(line)
                launch.log = (launch.log + line)[-100_000:]
                launch.heartbeat_at = timezone.now()
                launch.save(update_fields=["log", "heartbeat_at"])
                last_heartbeat = time.monotonic()
        elif time.monotonic() - last_heartbeat >= 15:
            launch.heartbeat_at = timezone.now()
            launch.save(update_fields=["heartbeat_at"])
            last_heartbeat = time.monotonic()
    remainder = process.stdout.read() if process.stdout else ""
    if remainder:
        captured.append(remainder)
        launch.log = (launch.log + remainder)[-100_000:]
    launch.heartbeat_at = timezone.now()
    launch.save(update_fields=["log", "heartbeat_at"])
    return ResearchCommandResult(process.returncode, "".join(captured))


@transaction.atomic
def queue_research_launch(
    franchise: Franchise,
    *,
    profile_id: str,
    known_legal_name: str,
    known_official_website: str,
    configuration: dict,
    requested_by=None,
    campaign=None,
    campaign_position: int | None = None,
) -> FranchiseResearchLaunch:
    if profile_id not in PROFILE_CHOICES:
        raise ResearchLaunchError("Nieobsługiwany profil researchu.")
    if franchise.research_launches.filter(status__in=["queued", "running"]).exists():
        raise ResearchLaunchError("Ta franczyza ma już aktywny pierwszy run.")
    try:
        return FranchiseResearchLaunch.objects.create(
            franchise=franchise,
            campaign=campaign,
            campaign_position=campaign_position,
            target_country="PL",
            profile_id=profile_id,
            known_legal_name=known_legal_name,
            known_official_website=known_official_website,
            configuration=configuration,
            requested_by=requested_by,
        )
    except IntegrityError as exc:
        raise ResearchLaunchError("Ta franczyza ma już aktywny pierwszy run.") from exc


def claim_next_launch() -> FranchiseResearchLaunch | None:
    with transaction.atomic():
        from .models import FranchiseResearchCampaign
        from .research_campaigns import campaign_launches_ready_for_claim, sync_campaign

        candidates = list(
            campaign_launches_ready_for_claim()
            .order_by("queued_at", "id")
            .values_list("pk", "campaign_id")[:200]
        )
        for launch_pk, campaign_pk in candidates:
            campaign = None
            if campaign_pk:
                campaign = FranchiseResearchCampaign.objects.select_for_update().get(
                    pk=campaign_pk
                )
                if campaign.cancel_requested:
                    continue
                running_count = campaign.launches.filter(
                    status=FranchiseResearchLaunch.STATUS_RUNNING
                ).count()
                if running_count >= campaign.max_concurrent_runs:
                    continue
            launch = (
                FranchiseResearchLaunch.objects.select_for_update()
                .filter(pk=launch_pk, status=FranchiseResearchLaunch.STATUS_QUEUED)
                .first()
            )
            if launch is None:
                continue
            now = timezone.now()
            launch.status = FranchiseResearchLaunch.STATUS_RUNNING
            launch.current_stage = "Walidacja konfiguracji"
            launch.progress_percent = 3
            launch.started_at = now
            launch.heartbeat_at = now
            launch.save(
                update_fields=[
                    "status",
                    "current_stage",
                    "progress_percent",
                    "started_at",
                    "heartbeat_at",
                ]
            )
            if campaign is not None:
                sync_campaign(campaign)
            return launch
        return None


def cancel_research_launch(launch: FranchiseResearchLaunch) -> None:
    if launch.status != FranchiseResearchLaunch.STATUS_QUEUED:
        raise ResearchLaunchError("Można anulować tylko zadanie oczekujące.")
    launch.status = FranchiseResearchLaunch.STATUS_CANCELLED
    launch.current_stage = "Anulowane przed uruchomieniem"
    launch.completed_at = timezone.now()
    launch.save(update_fields=["status", "current_stage", "completed_at"])
    if launch.campaign_id:
        from .research_campaigns import sync_campaign

        sync_campaign(launch.campaign)


@transaction.atomic
def retry_research_launch(launch: FranchiseResearchLaunch) -> None:
    locked = FranchiseResearchLaunch.objects.select_for_update().get(pk=launch.pk)
    if locked.status != FranchiseResearchLaunch.STATUS_FAILED:
        raise ResearchLaunchError("Można wznowić tylko run zakończony błędem.")
    if locked.franchise.research_launches.filter(
        status__in=[
            FranchiseResearchLaunch.STATUS_QUEUED,
            FranchiseResearchLaunch.STATUS_RUNNING,
        ]
    ).exclude(pk=locked.pk).exists():
        raise ResearchLaunchError("Ta franczyza ma już inny aktywny run.")
    if not locked.provider_failure_history:
        legacy_transient_code = _transient_provider_failure(locked.log)
        if legacy_transient_code:
            _record_unknown_provider_failure(
                locked,
                error_code=legacy_transient_code,
                stage=locked.current_stage or "Nieudany etap sprzed wdrożenia retry",
            )
    _apply_failure_history_reserve(locked)
    locked.status = FranchiseResearchLaunch.STATUS_QUEUED
    locked.current_stage = "Oczekiwanie na wznowienie od ostatniego artefaktu"
    locked.error_code = ""
    locked.error_message = ""
    locked.completed_at = None
    locked.heartbeat_at = None
    locked.log = (locked.log + "\n[Workbench] Run dodano ponownie do kolejki.\n")[-100_000:]
    locked.save(
        update_fields=[
            "status",
            "current_stage",
            "error_code",
            "error_message",
            "completed_at",
            "heartbeat_at",
            "log",
        ]
    )
    if locked.campaign_id:
        from .research_campaigns import sync_campaign

        sync_campaign(locked.campaign)


def _artifact_summary(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchLaunchError(
            f"Nie można odczytać zachowanego artefaktu {path.name}."
        ) from exc
    if not isinstance(value, dict):
        raise ResearchLaunchError(f"Zachowany artefakt {path.name} nie jest obiektem JSON.")
    return value


def fail_stale_launches(*, older_than: timedelta = timedelta(hours=2)) -> int:
    threshold = timezone.now() - older_than
    stale = FranchiseResearchLaunch.objects.filter(
        status=FranchiseResearchLaunch.STATUS_RUNNING,
        heartbeat_at__lt=threshold,
    )
    campaign_ids = list(
        stale.exclude(campaign__isnull=True).values_list("campaign_id", flat=True).distinct()
    )
    updated = stale.update(
        status=FranchiseResearchLaunch.STATUS_FAILED,
        current_stage="Worker przestał odpowiadać",
        error_code="stale_worker",
        error_message="Brak heartbeat workera w dozwolonym oknie.",
        completed_at=timezone.now(),
    )
    if campaign_ids:
        from .models import FranchiseResearchCampaign
        from .research_campaigns import sync_campaign

        for campaign in FranchiseResearchCampaign.objects.filter(pk__in=campaign_ids):
            sync_campaign(campaign)
    return updated


def _save_stage(launch, *, label: str, progress: int, usage: list[dict]) -> None:
    launch.current_stage = label
    launch.progress_percent = progress
    launch.cost_summary = _combined_usage(usage) if usage else {}
    launch.heartbeat_at = timezone.now()
    launch.save(update_fields=["current_stage", "progress_percent", "cost_summary", "heartbeat_at"])


def _run_stage(launch, runner, command, *, label, progress, summaries):
    _save_stage(launch, label=label, progress=progress, usage=summaries)
    configured_retries = launch.configuration.get(
        "transient_stage_retries",
        getattr(settings, "RESEARCH_TRANSIENT_STAGE_RETRIES", 2),
    )
    try:
        max_retries = max(0, min(int(configured_retries), 3))
    except (TypeError, ValueError):
        max_retries = 2
    retry_delay = max(
        0.0,
        min(
            float(getattr(settings, "RESEARCH_TRANSIENT_RETRY_DELAY_SECONDS", 2.0)),
            30.0,
        ),
    )
    retries_used = 0
    while True:
        result = runner(launch, command)
        if not result.returncode:
            summary = _parse_summary(result.stdout)
            summaries.append(summary)
            launch.cost_summary = _combined_usage(summaries)
            launch.save(update_fields=["cost_summary"])
            return summary

        transient_code = _transient_provider_failure(result.stdout)
        if transient_code is None or retries_used >= max_retries:
            if transient_code:
                _record_unknown_provider_failure(
                    launch,
                    error_code=transient_code,
                    stage=label,
                )
                summaries.append(_unknown_provider_attempt_summary(transient_code))
                launch.cost_summary = _combined_usage(summaries)
                launch.save(update_fields=["cost_summary"])
            detail = (
                f" Przejściowy błąd {transient_code} nie ustąpił po "
                f"{retries_used + 1} próbach."
                if transient_code
                else ""
            )
            raise ResearchLaunchError(
                f"Etap „{label}” zakończył się kodem {result.returncode}.{detail}"
            )

        _record_unknown_provider_failure(
            launch,
            error_code=transient_code,
            stage=label,
        )
        summaries.append(_unknown_provider_attempt_summary(transient_code))
        retries_used += 1
        launch.cost_summary = _combined_usage(summaries)
        launch.log = (
            launch.log
            + "\n[Worker] Przejściowy błąd providera "
            + f"({transient_code}); automatyczne ponowienie "
            + f"{retries_used}/{max_retries}. Nieznany koszt próby objęto rezerwą.\n"
        )[-100_000:]
        launch.heartbeat_at = timezone.now()
        launch.save(update_fields=["cost_summary", "log", "heartbeat_at"])
        try:
            _guard_budget(launch)
        except ResearchLaunchError as exc:
            raise ResearchLaunchError(
                f"Etap „{label}” napotkał {transient_code}; nie ponowiono, "
                f"ponieważ {exc}"
            ) from None
        if retry_delay:
            time.sleep(retry_delay * retries_used)


def _adopt_existing_stage(launch, path: Path, *, label, progress, summaries):
    _save_stage(launch, label=label, progress=progress, usage=summaries)
    summary = _artifact_summary(path)
    summaries.append(summary)
    launch.cost_summary = _combined_usage(summaries)
    launch.log = (
        launch.log + f"[Workbench] Wykorzystano istniejący artefakt: {path.name}.\n"
    )[-100_000:]
    launch.save(update_fields=["cost_summary", "log"])
    return summary


def _guard_budget(launch: FranchiseResearchLaunch) -> None:
    spent = _decimal(launch.cost_summary.get("budgeted_cost_usd"))
    if spent is None:
        spent = _decimal(launch.cost_summary.get("estimated_cost_usd"))
    limit = _decimal(launch.configuration.get("max_cost_usd"))
    if spent is None:
        raise ResearchLaunchError(
            "Koszt poprzedniego etapu jest nieznany; zatrzymano kolejne płatne wywołania."
        )
    if limit is not None and spent >= limit:
        raise ResearchLaunchError(
            f"Osiągnięto budżet pierwszego przebiegu (${spent} / ${limit})."
        )


def process_research_launch(
    launch: FranchiseResearchLaunch,
    *,
    runner: Callable[[FranchiseResearchLaunch, list[str]], ResearchCommandResult] = _default_runner,
) -> FranchiseResearchLaunch:
    if launch.status != FranchiseResearchLaunch.STATUS_RUNNING:
        raise ResearchLaunchError("Run musi zostać przejęty przez worker.")
    python = sys.executable
    config = launch.configuration
    summaries: list[dict] = _provider_failure_summaries(launch)
    try:
        if launch.plan_reference:
            plan_path = Path(launch.plan_reference).resolve()
            _adopt_existing_stage(
                launch,
                plan_path,
                label="Wznowienie / walidacja Planu",
                progress=8,
                summaries=summaries,
            )
        else:
            plan_command = [
                python, "-m", "datacollector", "plan",
                "--brand", launch.franchise.name,
                "--country", launch.target_country,
                "--profile", launch.profile_id,
                "--iteration", "1",
            ]
            if launch.known_legal_name:
                plan_command.extend(["--known-legal-name", launch.known_legal_name])
            if launch.known_official_website:
                plan_command.extend(["--known-official-website", launch.known_official_website])
            plan_summary = _run_stage(
                launch, runner, plan_command,
                label="Planner / definiowanie zakresu", progress=8, summaries=summaries,
            )
            plan_path = Path(plan_summary["plan_path"]).resolve()
        plan, launch.plan_sha256 = load_research_plan(plan_path)
        if plan.planner_input.brand_name != launch.franchise.name:
            raise ResearchLaunchError("Planner zwrócił artefakt dla innej marki.")
        launch.plan_reference = str(plan_path)
        launch.save(update_fields=["plan_reference", "plan_sha256"])
        _guard_budget(launch)

        if launch.sources_reference:
            sources_path = Path(launch.sources_reference).resolve()
            _adopt_existing_stage(
                launch,
                sources_path,
                label="Wznowienie / walidacja źródeł",
                progress=25,
                summaries=summaries,
            )
        else:
            search_summary = _run_stage(
                launch, runner,
                [
                    python, "-m", "datacollector", "search",
                    "--plan", str(plan_path),
                    "--iteration", "1",
                    "--limit-tasks", str(config["initial_task_limit"]),
                    "--max-search-calls", str(config["max_search_calls"]),
                    "--min-queries-per-task", "1",
                    "--max-candidate-routes", "5",
                ],
                label="Searcher / wyszukiwanie źródeł", progress=25, summaries=summaries,
            )
            sources_path = Path(search_summary["sources_path"]).resolve()
        search, launch.sources_sha256 = load_search_results(sources_path)
        if search.plan_sha256 != launch.plan_sha256:
            raise ResearchLaunchError("Searcher nie pochodzi z utworzonego Planu.")
        launch.sources_reference = str(sources_path)
        launch.save(update_fields=["sources_reference", "sources_sha256"])
        _guard_budget(launch)

        if launch.extractions_reference:
            extractions_path = Path(launch.extractions_reference).resolve()
            _adopt_existing_stage(
                launch,
                extractions_path,
                label="Wznowienie / walidacja ekstrakcji",
                progress=45,
                summaries=summaries,
            )
        else:
            extract_summary = _run_stage(
                launch, runner,
                [
                    python, "-m", "datacollector", "extract",
                    "--sources", str(sources_path),
                    "--iteration", "1",
                    "--limit-sources", str(config["max_sources"]),
                    "--max-api-calls", str(config["max_extractor_api_calls"]),
                    "--max-evidence-chars-per-call", "100000",
                ],
                label="Extractor / pobieranie i analiza dokumentów", progress=45, summaries=summaries,
            )
            extractions_path = Path(extract_summary["extractions_path"]).resolve()
        extraction, launch.extractions_sha256 = load_extraction_results(extractions_path)
        if extraction.search_sha256 != launch.sources_sha256:
            raise ResearchLaunchError("Extractor nie pochodzi z utworzonego Searchera.")
        launch.extractions_reference = str(extractions_path)
        launch.save(update_fields=["extractions_reference", "extractions_sha256"])
        _guard_budget(launch)

        if launch.check_reference:
            check_path = Path(launch.check_reference).resolve()
            _adopt_existing_stage(
                launch,
                check_path,
                label="Wznowienie / walidacja Checkera",
                progress=65,
                summaries=summaries,
            )
        else:
            check_summary = _run_stage(
                launch, runner,
                [
                    python, "-m", "datacollector", "check",
                    "--extractions", str(extractions_path),
                    "--iteration", "1",
                    "--max-claims", "500",
                    "--max-evidence-chars", "500000",
                ],
                label="Checker / pełna kontrola jakości", progress=65, summaries=summaries,
            )
            check_path = Path(check_summary["check_path"]).resolve()
        checker, launch.check_sha256 = load_checker_results(check_path)
        if checker.extraction_sha256 != launch.extractions_sha256:
            raise ResearchLaunchError("Checker nie pochodzi z utworzonego Extractora.")
        launch.check_reference = str(check_path)
        launch.save(update_fields=["check_reference", "check_sha256"])
        _guard_budget(launch)

        if launch.normalized_reference:
            normalized_path = Path(launch.normalized_reference).resolve()
            _adopt_existing_stage(
                launch,
                normalized_path,
                label="Wznowienie / walidacja Normalizera",
                progress=82,
                summaries=summaries,
            )
        else:
            normalize_command = [
                python, "-m", "datacollector", "normalize",
                "--check", str(check_path),
                "--iteration", "1",
                "--max-claims", "500",
                "--max-input-chars", "500000",
            ]
            if not checker.passed:
                normalize_command.append("--allow-incomplete")
            normalized_summary = _run_stage(
                launch, runner, normalize_command,
                label="Normalizer / przygotowanie draftu", progress=82, summaries=summaries,
            )
            normalized_path = Path(normalized_summary["normalized_path"]).resolve()
        normalized, launch.normalized_sha256 = load_normalizer_results(normalized_path)
        if normalized.check_sha256 != launch.check_sha256:
            raise ResearchLaunchError("Normalizer nie pochodzi z utworzonego Checkera.")
        launch.normalized_reference = str(normalized_path)
        launch.save(update_fields=["normalized_reference", "normalized_sha256"])

        _save_stage(
            launch,
            label="Workbench / materializacja pól i dowodów",
            progress=94,
            usage=summaries,
        )
        workspace, _ = create_research_workspace(
            normalized_path,
            franchise_slug=launch.franchise.slug,
            created_by=launch.requested_by,
        )
        launch.result_workspace = workspace
        launch.status = FranchiseResearchLaunch.STATUS_SUCCEEDED
        launch.current_stage = "Workbench gotowy do Human Review"
        launch.progress_percent = 100
        launch.result_summary = {
            "profile_id": workspace.profile_id,
            "workspace_id": str(workspace.workspace_id),
            "planned_tasks": workspace.planned_tasks,
            "evaluated_tasks": workspace.evaluated_tasks,
            "planned_fields": workspace.planned_fields,
            "quality_score": workspace.quality_score,
        }
        launch.cost_summary = _combined_usage(summaries)
        launch.completed_at = timezone.now()
        launch.heartbeat_at = launch.completed_at
        launch.save()
    except Exception as exc:
        launch.status = FranchiseResearchLaunch.STATUS_FAILED
        launch.current_stage = "Zatrzymane z błędem"
        launch.error_code = type(exc).__name__[:80]
        launch.error_message = str(exc)[:4000]
        launch.completed_at = timezone.now()
        launch.heartbeat_at = launch.completed_at
        launch.save()
    if launch.campaign_id:
        from .research_campaigns import sync_campaign

        sync_campaign(launch.campaign)
    return launch
