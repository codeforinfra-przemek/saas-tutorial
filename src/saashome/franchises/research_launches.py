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
from django.db import DatabaseError, IntegrityError, close_old_connections, transaction
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
from datacollector.benchmark import field_policy_map
from datacollector.schemas import NormalizerMode, NormalizerStrategySource

from .models import Franchise, FranchiseResearchLaunch
from .research_fields import L1_AUTO_REVIEW_POLICY_VERSION, L1_PUBLIC_FIELD_ORDER
from .research_jobs import ResearchCommandResult
from .research_workbench import create_research_workspace


PROFILE_CHOICES = frozenset({"PL:L1", "PL:L2", "PL:L3"})
UNKNOWN_PROVIDER_ATTEMPT_RESERVE_USD = Decimal("0.50")


class ResearchLaunchError(ValueError):
    """Safe orchestration or immutable-lineage error."""


def _normalizer_allows_publication(normalized) -> bool:
    """Mirror the immutable import gate before mutating Auto-review state."""

    return (
        normalized.normalization_mode == NormalizerMode.PAID
        and normalized.strategy_source
        in {
            NormalizerStrategySource.OPENAI,
            NormalizerStrategySource.OPENAI_REPAIRED,
        }
    )


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
    summaries = []
    for item in launch.provider_failure_history or []:
        usage = item.get("usage")
        if isinstance(usage, dict):
            summaries.append({"agent_usage": [usage], "failed_attempts": []})
        elif item.get("token_usage_unknown", True):
            summaries.append(
                _unknown_provider_attempt_summary(
                    item.get("error_code") or "provider_exception"
                )
            )
    return summaries


_FAILURE_LEDGER_REFERENCE = re.compile(
    r"(?:Failure ledger|Provider usage saved to):\s*([^\n]+)"
)


def _failure_ledger_paths(output: str) -> list[Path]:
    """Return only datacollector attempt ledgers inside this repository."""

    paths = []
    seen = set()
    repository_root = REPOSITORY_ROOT.resolve()
    for match in _FAILURE_LEDGER_REFERENCE.finditer(output or ""):
        for raw_value in match.group(1).split(","):
            raw_path = raw_value.strip().rstrip(".")
            if not raw_path:
                continue
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = repository_root / candidate
            try:
                candidate = candidate.resolve()
                candidate.relative_to(repository_root)
            except (OSError, ValueError):
                continue
            if (
                candidate.parent.name != "attempts"
                or candidate.suffix != ".json"
                or candidate in seen
                or not candidate.is_file()
            ):
                continue
            paths.append(candidate)
            seen.add(candidate)
    return paths


def _capture_failure_ledgers(
    launch: FranchiseResearchLaunch,
    output: str,
    *,
    stage: str,
) -> list[dict]:
    """Persist paid failure usage so retries and campaign totals cannot lose it."""

    history = list(launch.provider_failure_history or [])
    known_paths = {item.get("ledger_path") for item in history}
    summaries = []
    changed = False
    for path in _failure_ledger_paths(output):
        path_value = str(path)
        if path_value in known_paths:
            continue
        try:
            ledger = _artifact_summary(path)
        except ResearchLaunchError:
            continue
        usage = ledger.get("usage")
        usage = usage if isinstance(usage, dict) else None
        error_code = str(ledger.get("error_code") or "provider_exception")[:80]
        history.append(
            {
                "error_code": error_code,
                "stage": stage,
                "usage_recorded": usage is not None,
                "token_usage_unknown": usage is None,
                "ledger_path": path_value,
                "usage": usage,
                "recorded_at": timezone.now().isoformat(),
            }
        )
        summaries.append(
            {"agent_usage": [usage], "failed_attempts": []}
            if usage is not None
            else _unknown_provider_attempt_summary(error_code)
        )
        known_paths.add(path_value)
        changed = True
    if changed:
        launch.provider_failure_history = history[-50:]
        launch.save(update_fields=["provider_failure_history"])
    return summaries


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


def _usage_with_floor(launch, stage_summaries: list[dict]) -> dict:
    """Add only work executed after a retry to its persisted cost ledger."""

    calculated = _combined_usage(stage_summaries)
    floor = getattr(launch, "_usage_floor", None) or {}
    if not floor:
        return calculated
    integer_keys = (
        "api_attempts_recorded",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "tool_calls",
        "unknown_cost_attempts",
    )
    for key in integer_keys:
        calculated[key] = (
            int(calculated.get(key) or 0)
            + int(floor.get(key) or 0)
        )
    for key in (
        "tool_cost_usd",
        "estimated_cost_usd",
        "unknown_cost_reserve_usd",
        "budgeted_cost_usd",
    ):
        calculated[key] = str(
            (_decimal(calculated.get(key)) or Decimal("0"))
            + (_decimal(floor.get(key)) or Decimal("0"))
        )
    calculated["cost_complete"] = bool(
        calculated.get("cost_complete", True)
        and floor.get("cost_complete", True)
    )
    return calculated


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
    website_seed = known_official_website.strip() or franchise.website_url.strip()
    try:
        return FranchiseResearchLaunch.objects.create(
            franchise=franchise,
            campaign=campaign,
            campaign_position=campaign_position,
            target_country="PL",
            profile_id=profile_id,
            known_legal_name=known_legal_name,
            known_official_website=website_seed,
            configuration={
                **configuration,
                "website_seed_trust": (
                    "validated_official"
                    if website_seed
                    and franchise.website_url_status == Franchise.WEBSITE_VALIDATED
                    else "unverified_seed"
                    if website_seed
                    else "missing"
                ),
                "l1_pipeline_version": (
                    "cheap-effective-v3"
                    if profile_id in {"PL:L1", "PL:L1:v2"}
                    else "standard-v1"
                ),
            },
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
            update_fields = [
                "status",
                "current_stage",
                "progress_percent",
                "heartbeat_at",
            ]
            if launch.started_at is None:
                launch.started_at = now
                update_fields.append("started_at")
            launch.heartbeat_at = now
            launch.save(update_fields=update_fields)
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
    captured = _capture_failure_ledgers(
        locked,
        locked.log,
        stage=locked.current_stage or "Nieudany etap sprzed wdrożenia retry",
    )
    if captured:
        locked.cost_summary = _combined_usage(
            [{"usage_totals": locked.cost_summary}, *captured]
        )
        locked.save(update_fields=["cost_summary"])
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


_SOURCES_PATH_REFERENCE = re.compile(r'"sources_path"\s*:\s*"([^"\n]+)"')


def _recover_paid_search_artifact(
    launch: FranchiseResearchLaunch,
    *,
    plan_sha256: str,
) -> Path | None:
    """Adopt a valid paid Searcher artifact left behind by a crashed parent worker."""

    repository_root = REPOSITORY_ROOT.resolve()
    raw_paths = _SOURCES_PATH_REFERENCE.findall(launch.log or "")
    for raw_path in reversed(raw_paths):
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = repository_root / candidate
        try:
            candidate = candidate.resolve()
            candidate.relative_to(repository_root)
        except (OSError, ValueError):
            continue
        if not candidate.is_file():
            continue
        try:
            search, artifact_sha256 = load_search_results(candidate)
        except Exception:
            continue
        if search.generated_by != "openai" or search.plan_sha256 != plan_sha256:
            continue
        launch.sources_reference = str(candidate)
        launch.sources_sha256 = artifact_sha256
        launch.log = (
            launch.log
            + "\n[Worker] Odzyskano poprawny płatny artefakt Searchera po "
            + "błędzie procesu nadrzędnego; zapytanie nie będzie ponawiane.\n"
        )[-100_000:]
        launch.save(update_fields=["sources_reference", "sources_sha256", "log"])
        return candidate
    return None


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
    launch.cost_summary = _usage_with_floor(launch, usage)
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
            launch.cost_summary = _usage_with_floor(launch, summaries)
            launch.save(update_fields=["cost_summary"])
            return summary

        captured_failures = _capture_failure_ledgers(
            launch,
            result.stdout,
            stage=label,
        )
        if captured_failures:
            summaries.extend(captured_failures)
            launch.cost_summary = _usage_with_floor(launch, summaries)
            launch.save(update_fields=["cost_summary"])
        transient_code = _transient_provider_failure(result.stdout)
        if transient_code is None or retries_used >= max_retries:
            if transient_code and not captured_failures:
                _record_unknown_provider_failure(
                    launch,
                    error_code=transient_code,
                    stage=label,
                )
                summaries.append(_unknown_provider_attempt_summary(transient_code))
                launch.cost_summary = _usage_with_floor(launch, summaries)
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

        if not captured_failures:
            _record_unknown_provider_failure(
                launch,
                error_code=transient_code,
                stage=label,
            )
            summaries.append(_unknown_provider_attempt_summary(transient_code))
        retries_used += 1
        launch.cost_summary = _usage_with_floor(launch, summaries)
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
    # On retry the persisted floor already contains this stage. Re-reading the
    # immutable artifact validates lineage, but must not bill it twice.
    if not getattr(launch, "_usage_floor", None):
        summaries.append(summary)
    launch.cost_summary = _usage_with_floor(launch, summaries)
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


def _accepted_checker_fields(checker) -> set[str]:
    return {
        decision.target_field
        for decision in checker.claim_decisions
        if decision.verdict.value == "accepted"
    }


_L1_LOW_RISK_CANDIDATE_FIELDS = frozenset(
    {
        "brand.name",
        "brand.public_summary",
        "contact.generic_business_route",
        "offer.unit_formats",
        "candidate.premises_requirements",
        "support.training_program",
    }
)


def _actionable_l1_checker_fields(checker) -> set[str]:
    """Count safe grounded fields even when risk-based Checker skips semantics."""

    fields = _accepted_checker_fields(checker)
    policies = field_policy_map(getattr(checker, "profile_id", None))
    assessments = {
        item.source_id: item
        for item in checker.source_assessments
    }
    for decision in checker.claim_decisions:
        if (
            decision.verdict.value != "not_reviewed"
            or decision.target_field not in _L1_LOW_RISK_CANDIDATE_FIELDS
            or decision.target_field not in policies
        ):
            continue
        policy = policies[decision.target_field]
        supported = {
            source_id
            for source_id in decision.source_ids
            if source_id in assessments
            and assessments[source_id].source_type.value
            in policy.accepted_source_types
        }
        if len(supported) >= policy.minimum_sources:
            fields.add(decision.target_field)
    return fields


def _process_hybrid_l1_discovery(
    launch: FranchiseResearchLaunch,
    runner,
    *,
    python: str,
    plan,
    plan_path: Path,
    config: dict,
    summaries: list[dict],
) -> tuple[Path, Path, Path | None, dict]:
    """Run official-first L1 and search only Checker/Resolver-defined gaps."""

    cache_dir = (
        Path(__file__).resolve().parents[3]
        / "datacollector"
        / "data"
        / "document_cache"
    )
    if launch.seed_sources_reference:
        seed_sources_path = Path(launch.seed_sources_reference).resolve()
        _adopt_existing_stage(
            launch,
            seed_sources_path,
            label="Wznowienie / oficjalne seedy L1",
            progress=14,
            summaries=summaries,
        )
    else:
        seed_search_summary = _run_stage(
            launch,
            runner,
            [
                python,
                "-m",
                "datacollector",
                "search",
                "--plan",
                str(plan_path),
                "--iteration",
                "1",
                "--limit-tasks",
                str(config["initial_task_limit"]),
                "--max-search-calls",
                "1",
                "--min-queries-per-task",
                "1",
                "--max-candidate-routes",
                "0",
                "--official-first",
                "--offline",
            ],
            label="L1/2A · oficjalne seedy bez Searchera",
            progress=14,
            summaries=summaries,
        )
        seed_sources_path = Path(seed_search_summary["sources_path"]).resolve()
        launch.seed_sources_reference = str(seed_sources_path)
        launch.save(update_fields=["seed_sources_reference"])
    seed_search, seed_search_sha256 = load_search_results(seed_sources_path)
    if seed_search.plan_sha256 != launch.plan_sha256:
        raise ResearchLaunchError("Oficjalne seedy nie pochodzą z utworzonego Planu.")

    if launch.seed_extractions_reference:
        seed_extractions_path = Path(launch.seed_extractions_reference).resolve()
        _adopt_existing_stage(
            launch,
            seed_extractions_path,
            label="Wznowienie / ekstrakcja oficjalnych seedów",
            progress=31,
            summaries=summaries,
        )
    else:
        seed_extract_summary = _run_stage(
            launch,
            runner,
            [
                python,
                "-m",
                "datacollector",
                "extract",
                "--sources",
                str(seed_sources_path),
                "--iteration",
                "1",
                "--limit-sources",
                str(min(5, config["max_sources"])),
                "--max-api-calls",
                str(min(5, config["max_extractor_api_calls"])),
                "--max-evidence-chars-per-call",
                "50000",
                "--document-cache-dir",
                str(cache_dir),
            ],
            label="L1/2A · pobranie, cache i ekstrakcja stron oficjalnych",
            progress=31,
            summaries=summaries,
        )
        seed_extractions_path = Path(
            seed_extract_summary["extractions_path"]
        ).resolve()
        launch.seed_extractions_reference = str(seed_extractions_path)
        launch.save(update_fields=["seed_extractions_reference"])
    seed_extraction, seed_extraction_sha256 = load_extraction_results(
        seed_extractions_path
    )
    if seed_extraction.search_sha256 != seed_search_sha256:
        raise ResearchLaunchError("Ekstrakcja seedów nie pochodzi z ich Searchera.")
    _guard_budget(launch)

    if launch.seed_check_reference:
        seed_check_path = Path(launch.seed_check_reference).resolve()
        _adopt_existing_stage(
            launch,
            seed_check_path,
            label="Wznowienie / kontrola seedów L1",
            progress=43,
            summaries=summaries,
        )
    else:
        seed_check_summary = _run_stage(
            launch,
            runner,
            [
                python,
                "-m",
                "datacollector",
                "check",
                "--extractions",
                str(seed_extractions_path),
                "--iteration",
                "1",
                "--max-claims",
                "200",
                "--max-evidence-chars",
                "250000",
                "--risk-based",
            ],
            label="L1/2A · kontrola ryzyka bez audytu pól niskiego ryzyka",
            progress=43,
            summaries=summaries,
        )
        seed_check_path = Path(seed_check_summary["check_path"]).resolve()
        launch.seed_check_reference = str(seed_check_path)
        launch.save(update_fields=["seed_check_reference"])
    seed_checker, _ = load_checker_results(seed_check_path)
    accepted_seed_fields = _actionable_l1_checker_fields(seed_checker)
    minimum_l1_proposals = 8
    if len(accepted_seed_fields) >= minimum_l1_proposals:
        launch.sources_reference = str(seed_sources_path)
        launch.sources_sha256 = seed_search_sha256
        launch.extractions_reference = str(seed_extractions_path)
        launch.extractions_sha256 = seed_extraction_sha256
        launch.check_reference = str(seed_check_path)
        _, launch.check_sha256 = load_checker_results(seed_check_path)
        launch.save(
            update_fields=[
                "sources_reference",
                "sources_sha256",
                "extractions_reference",
                "extractions_sha256",
                "check_reference",
                "check_sha256",
            ]
        )
        return (
            seed_sources_path,
            seed_extractions_path,
            seed_check_path,
            {
                "route": "official_only",
                "seed_accepted_fields": len(accepted_seed_fields),
                "paid_search_skipped": True,
            },
        )

    if launch.resolution_reference:
        resolution_path = Path(launch.resolution_reference).resolve()
        _adopt_existing_stage(
            launch,
            resolution_path,
            label="Wznowienie / plan braków L1",
            progress=48,
            summaries=summaries,
        )
    else:
        resolution_summary = _run_stage(
            launch,
            runner,
            [
                python,
                "-m",
                "datacollector",
                "resolve",
                "--check",
                str(seed_check_path),
                "--free",
                "--iteration",
                "1",
                "--max-follow-ups",
                "30",
                "--max-source-actions",
                str(config["max_sources"]),
                "--max-search-tasks",
                str(config["initial_task_limit"]),
                "--max-queries-per-item",
                "2",
                "--prefer-new-search",
            ],
            label="L1/2B · deterministyczny plan wyłącznie dla braków",
            progress=48,
            summaries=summaries,
        )
        resolution_path = Path(resolution_summary["resolution_path"]).resolve()
        launch.resolution_reference = str(resolution_path)
        launch.save(update_fields=["resolution_reference"])

    if launch.execution_reference:
        execution_path = Path(launch.execution_reference).resolve()
        execution_summary = _adopt_existing_stage(
            launch,
            execution_path,
            label="Wznowienie / scalony research braków L1",
            progress=63,
            summaries=summaries,
        )
    else:
        execution_summary = _run_stage(
            launch,
            runner,
            [
                python,
                "-m",
                "datacollector",
                "execute",
                "--resolution",
                str(resolution_path),
                "--iteration",
                "2",
                "--max-search-calls",
                str(config["max_search_calls"]),
                "--min-queries-per-task",
                "1",
                "--max-candidate-routes",
                "3",
                "--max-extractor-api-calls",
                str(config["max_extractor_api_calls"]),
                "--max-evidence-chars-per-call",
                "75000",
                "--document-cache-dir",
                str(cache_dir),
            ],
            label="L1/2B · Searcher braków i scalona ekstrakcja",
            progress=63,
            summaries=summaries,
        )
        execution_path = Path(execution_summary["execution_path"]).resolve()
        launch.execution_reference = str(execution_path)
        launch.save(update_fields=["execution_reference"])
    sources_path = Path(execution_summary["sources_path"]).resolve()
    extractions_path = Path(execution_summary["extractions_path"]).resolve()
    search, search_sha256 = load_search_results(sources_path)
    extraction, extraction_sha256 = load_extraction_results(extractions_path)
    if search.plan_sha256 != launch.plan_sha256:
        raise ResearchLaunchError("Scalony Searcher L1 nie pochodzi z Planu.")
    if extraction.search_sha256 != search_sha256:
        raise ResearchLaunchError("Scalony Extractor L1 nie pochodzi z Searchera.")
    launch.sources_reference = str(sources_path)
    launch.sources_sha256 = search_sha256
    launch.extractions_reference = str(extractions_path)
    launch.extractions_sha256 = extraction_sha256
    launch.save(
        update_fields=[
            "sources_reference",
            "sources_sha256",
            "extractions_reference",
            "extractions_sha256",
        ]
    )
    return (
        sources_path,
        extractions_path,
        None,
        {
            "route": "official_plus_gap_search",
            "seed_accepted_fields": len(accepted_seed_fields),
            "paid_search_skipped": False,
        },
    )


def process_research_launch(
    launch: FranchiseResearchLaunch,
    *,
    runner: Callable[[FranchiseResearchLaunch, list[str]], ResearchCommandResult] = _default_runner,
) -> FranchiseResearchLaunch:
    if launch.status != FranchiseResearchLaunch.STATUS_RUNNING:
        raise ResearchLaunchError("Run musi zostać przejęty przez worker.")
    launch._usage_floor = dict(launch.cost_summary or {})
    python = sys.executable
    config = dict(launch.configuration)
    if (
        launch.profile_id in {"PL:L1", "PL:L1:v2"}
        and config.get("l1_pipeline_version") != "cheap-effective-v3"
    ):
        config["l1_pipeline_version"] = "cheap-effective-v3"
        launch.configuration = config
        launch.save(update_fields=["configuration"])
    summaries: list[dict] = (
        [] if launch._usage_floor else _provider_failure_summaries(launch)
    )
    hybrid_summary: dict = {}
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
            if launch.profile_id in {"PL:L1", "PL:L1:v2"}:
                plan_command.append("--offline")
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
        if not launch.sources_reference:
            _recover_paid_search_artifact(
                launch,
                plan_sha256=launch.plan_sha256,
            )
        _guard_budget(launch)

        prebuilt_extractions_path: Path | None = None
        prebuilt_check_path: Path | None = None
        is_hybrid_l1 = (
            launch.profile_id in {"PL:L1", "PL:L1:v2"}
            and bool(launch.known_official_website)
        )
        if launch.sources_reference:
            sources_path = Path(launch.sources_reference).resolve()
            _adopt_existing_stage(
                launch,
                sources_path,
                label="Wznowienie / walidacja źródeł",
                progress=25,
                summaries=summaries,
            )
        elif is_hybrid_l1:
            (
                sources_path,
                prebuilt_extractions_path,
                prebuilt_check_path,
                hybrid_summary,
            ) = _process_hybrid_l1_discovery(
                launch,
                runner,
                python=python,
                plan=plan,
                plan_path=plan_path,
                config=config,
                summaries=summaries,
            )
        else:
            # Searcher v4 requires one task-specific query action for every
            # selected task.  The previous official-first optimization reduced
            # this cap to four even when PL:L1 selected seven tasks, making the
            # final investment/training/outlet tasks impossible to search.  A
            # known website reduces retrieval work, not the explicit paid tool
            # ceiling requested by the campaign operator.
            paid_search_calls = int(config["max_search_calls"])
            search_summary = _run_stage(
                launch, runner,
                [
                    python, "-m", "datacollector", "search",
                    "--plan", str(plan_path),
                    "--iteration", "1",
                    "--limit-tasks", str(config["initial_task_limit"]),
                    "--max-search-calls", str(paid_search_calls),
                    "--min-queries-per-task", "1",
                    "--max-candidate-routes", "5",
                    "--official-first",
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

        if prebuilt_extractions_path is not None:
            extractions_path = prebuilt_extractions_path
        elif launch.extractions_reference:
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
                    "--document-cache-dir", str(
                        Path(__file__).resolve().parents[3]
                        / "datacollector" / "data" / "document_cache"
                    ),
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

        if prebuilt_check_path is not None:
            check_path = prebuilt_check_path
        elif launch.check_reference:
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
                    "--iteration", str(extraction.iteration),
                    "--max-claims", "500",
                    "--max-evidence-chars", "500000",
                    *(
                        ["--risk-based"]
                        if launch.profile_id in {"PL:L1", "PL:L1:v2"}
                        else []
                    ),
                ],
                label=(
                    "Checker / kontrola ryzyka L1"
                    if launch.profile_id in {"PL:L1", "PL:L1:v2"}
                    else "Checker / pełna kontrola jakości"
                ),
                progress=65,
                summaries=summaries,
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
                "--iteration", str(checker.iteration),
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
        # The final merged artifacts intentionally retain only their own paid
        # usage plus immutable predecessor references.  The launcher has the
        # complete official-seed + gap-repair ledger, so the Workbench must use
        # that total instead of under-reporting the Stage 2 cost.
        workspace.cost_summary = dict(launch.cost_summary)
        workspace.save(update_fields=["cost_summary", "updated_at"])
        launch.result_workspace = workspace
        proposed_fields = workspace.review_fields.exclude(
            proposed_values=[]
        ).values("target_field").distinct().count()
        projectable_fields = len(
            {
                target_field
                for target_field in workspace.review_fields.exclude(
                    proposed_values=[]
                ).values_list("target_field", flat=True)
                if target_field in L1_PUBLIC_FIELD_ORDER
            }
        )
        minimum_l1_proposals = 8
        is_l1 = launch.profile_id in {"PL:L1", "PL:L1:v2"}
        if is_l1 and proposed_fields <= 2:
            launch.status = FranchiseResearchLaunch.STATUS_INSUFFICIENT
            completion = "insufficient"
            launch.current_stage = (
                "Niewystarczający L1 — Workbench wymaga uzupełnienia"
            )
        elif is_l1 and (
            not workspace.scope_complete
            or proposed_fields < minimum_l1_proposals
        ):
            launch.status = FranchiseResearchLaunch.STATUS_PARTIAL
            completion = "partial"
            launch.current_stage = (
                "Częściowy L1 — Workbench gotowy do Human Review"
            )
        elif workspace.scope_complete:
            launch.status = FranchiseResearchLaunch.STATUS_COMPLETE
            completion = "complete"
            launch.current_stage = "Pełny L1 — Workbench gotowy do Human Review"
        else:
            launch.status = FranchiseResearchLaunch.STATUS_PARTIAL
            completion = "partial"
            launch.current_stage = (
                "Częściowy wynik — Workbench gotowy do Human Review"
            )
        auto_review_summary = {}
        finalization_summary = {}
        if is_l1 and config.get("auto_review_finalize"):
            if not _normalizer_allows_publication(normalized):
                auto_review_summary = {
                    "policy_version": L1_AUTO_REVIEW_POLICY_VERSION,
                    "contract_fields": len(L1_PUBLIC_FIELD_ORDER),
                    "policy_accepted": 0,
                    "documented_gaps": 0,
                    "pending_human_review": projectable_fields,
                    "accepted_fields": [],
                    "gap_fields": [],
                    "pending_fields": [],
                    "pending_reasons": {},
                    "skipped_reason": "normalizer_not_import_eligible",
                }
                finalization_summary = {
                    "skipped_reason": "normalizer_not_import_eligible",
                }
                launch.current_stage = (
                    "L1 bez publikacji — Normalizer nie zwrócił wyniku "
                    "zdatnego do importu"
                )
            else:
                from .research_auto_review import auto_review_l1_workspace
                from .research_finalizer import finalize_research_workspace

                auto_review_summary = auto_review_l1_workspace(
                    workspace,
                    actor=launch.requested_by,
                )
                if auto_review_summary["policy_accepted"]:
                    finalization, _ = finalize_research_workspace(
                        workspace,
                        actor=launch.requested_by,
                    )
                    published = list(
                        finalization.published_fields.filter(
                            status="projected",
                            is_current=True,
                        ).values_list("target_field", flat=True)
                    )
                    finalization_summary = {
                        "finalization_id": str(finalization.finalization_id),
                        "release_number": finalization.release_number,
                        "published_fields": len(published),
                        "published_target_fields": published,
                    }
                    launch.current_stage = (
                        f"{launch.current_stage.split(' — Workbench')[0]} — "
                        f"opublikowano automatycznie {len(published)}/20 "
                        "bezpiecznych pól"
                    )
                else:
                    launch.current_stage = (
                        f"{launch.current_stage} — "
                        "auto-review nie dopuścił żadnego pola"
                    )
        launch.progress_percent = 100
        launch.result_summary = {
            "profile_id": workspace.profile_id,
            "l1_pipeline_version": (
                "cheap-effective-v3"
                if launch.profile_id in {"PL:L1", "PL:L1:v2"}
                else None
            ),
            "l1_route": hybrid_summary.get("route"),
            "seed_accepted_fields": hybrid_summary.get("seed_accepted_fields"),
            "paid_search_skipped": hybrid_summary.get("paid_search_skipped"),
            "workspace_id": str(workspace.workspace_id),
            "planned_tasks": workspace.planned_tasks,
            "evaluated_tasks": workspace.evaluated_tasks,
            "planned_fields": workspace.planned_fields,
            "proposed_fields": proposed_fields,
            "projectable_fields": projectable_fields,
            "auto_review": auto_review_summary,
            "auto_finalization": finalization_summary,
            "minimum_proposed_fields": minimum_l1_proposals if is_l1 else None,
            "selected_sources": len(search.sources),
            "selected_documents": len(extraction.documents),
            "parsed_documents": sum(
                document.parse_status.value in {"parsed", "partial"}
                for document in extraction.documents
            ),
            "claims": len(extraction.claims),
            "accepted_claims": sum(
                decision.verdict.value == "accepted"
                for decision in checker.claim_decisions
            ),
            "needs_review_claims": sum(
                decision.verdict.value == "needs_review"
                for decision in checker.claim_decisions
            ),
            "rejected_claims": sum(
                decision.verdict.value == "rejected"
                for decision in checker.claim_decisions
            ),
            "normalized_values": len(normalized.normalized_values),
            "quality_score": workspace.quality_score,
            "completion": completion,
        }
        launch.cost_summary = _usage_with_floor(launch, summaries)
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
        try:
            launch.save()
        except DatabaseError:
            # A long-running worker may lose its serverless PostgreSQL
            # connection while OpenAI and document retrieval are in progress.
            # Reconnect once so the durable lease is not left as "running".
            close_old_connections()
            FranchiseResearchLaunch.objects.filter(pk=launch.pk).update(
                status=launch.status,
                current_stage=launch.current_stage,
                error_code=launch.error_code,
                error_message=launch.error_message,
                completed_at=launch.completed_at,
                heartbeat_at=launch.heartbeat_at,
                cost_summary=launch.cost_summary,
                log=launch.log,
            )
            launch = FranchiseResearchLaunch.objects.get(pk=launch.pk)
    if launch.campaign_id:
        from .research_campaigns import sync_campaign

        sync_campaign(launch.campaign)
    return launch
