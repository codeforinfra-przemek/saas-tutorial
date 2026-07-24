"""Safe batch orchestration for first-run franchise research."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Franchise, FranchiseResearchCampaign, FranchiseResearchLaunch
from .research_fields import L1_PUBLIC_FIELD_ORDER
from .research_launches import (
    PROFILE_CHOICES,
    ResearchLaunchError,
    cancel_research_launch,
    queue_research_launch,
    retry_research_launch,
)


class ResearchCampaignError(ValueError):
    """A safe, staff-facing campaign validation error."""


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def campaign_snapshot(campaign: FranchiseResearchCampaign) -> dict:
    launches = list(
        campaign.launches.select_related("franchise", "result_workspace").order_by(
            "campaign_position", "id"
        )
    )
    statuses = {
        choice[0]: sum(1 for launch in launches if launch.status == choice[0])
        for choice in FranchiseResearchLaunch.STATUS_CHOICES
    }
    estimated_cost = sum(
        (_decimal(launch.cost_summary.get("estimated_cost_usd")) for launch in launches),
        Decimal("0"),
    )
    budgeted_cost = sum(
        (
            _decimal(
                launch.cost_summary.get("budgeted_cost_usd")
                or launch.cost_summary.get("estimated_cost_usd")
            )
            for launch in launches
        ),
        Decimal("0"),
    )
    tokens = sum(int(launch.cost_summary.get("total_tokens") or 0) for launch in launches)
    unknown_attempts = sum(
        int(launch.cost_summary.get("unknown_cost_attempts") or 0)
        for launch in launches
    )
    total = len(launches)
    complete = statuses[FranchiseResearchLaunch.STATUS_COMPLETE]
    partial = statuses[FranchiseResearchLaunch.STATUS_PARTIAL]
    insufficient = statuses[FranchiseResearchLaunch.STATUS_INSUFFICIENT]
    legacy_succeeded = statuses[FranchiseResearchLaunch.STATUS_SUCCEEDED]
    completed = complete + partial + insufficient + legacy_succeeded
    failed = statuses[FranchiseResearchLaunch.STATUS_FAILED]
    cancelled = statuses[FranchiseResearchLaunch.STATUS_CANCELLED]
    terminal = completed + failed + cancelled
    proposed_fields = 0
    projectable_fields = 0
    planned_fields = 0
    normalized_values = 0
    selected_documents = 0
    parsed_documents = 0
    claims = 0
    accepted_claims = 0
    needs_review_claims = 0
    rejected_claims = 0
    auto_finalized = 0
    published_fields = 0
    for launch in launches:
        planned_tasks = int(launch.result_summary.get("planned_tasks") or 0)
        evaluated_tasks = int(launch.result_summary.get("evaluated_tasks") or 0)
        launch.scope_label = (
            f"zakres {evaluated_tasks}/{planned_tasks}"
            if planned_tasks
            else "zakres jeszcze nieustalony"
        )
        launch.proposed_field_count = 0
        launch.projectable_field_count = 0
        if launch.result_workspace_id:
            targets = list(
                launch.result_workspace.review_fields.exclude(
                    proposed_values=[]
                ).values_list("target_field", flat=True)
            )
            launch.proposed_field_count = len(set(targets))
            launch.projectable_field_count = len(
                {
                    target
                    for target in targets
                    if target in L1_PUBLIC_FIELD_ORDER
                }
            )
            if hasattr(launch.result_workspace, "finalization"):
                auto_finalized += int(launch.result_workspace.auto_reviewed)
                published_fields += (
                    launch.result_workspace.finalization.published_fields.filter(
                        status="projected",
                        is_current=True,
                    ).count()
                )
        proposed_fields += launch.proposed_field_count
        projectable_fields += launch.projectable_field_count
        if launch.result_workspace_id:
            planned_fields += int(launch.result_workspace.planned_fields or 0)
            normalized_values += int(
                launch.result_workspace.normalized_values_count or 0
            )
        selected_documents += int(
            launch.result_summary.get("selected_documents") or 0
        )
        parsed_documents += int(
            launch.result_summary.get("parsed_documents") or 0
        )
        claims += int(launch.result_summary.get("claims") or 0)
        accepted_claims += int(
            launch.result_summary.get("accepted_claims") or 0
        )
        needs_review_claims += int(
            launch.result_summary.get("needs_review_claims") or 0
        )
        rejected_claims += int(
            launch.result_summary.get("rejected_claims") or 0
        )
    progress = round(
        sum(int(launch.progress_percent or 0) for launch in launches) / total
    ) if total else 0
    return {
        "total": total,
        "queued": statuses[FranchiseResearchLaunch.STATUS_QUEUED],
        "running": statuses[FranchiseResearchLaunch.STATUS_RUNNING],
        "succeeded": completed,
        "complete": complete,
        "partial": partial,
        "insufficient": insufficient,
        "failed": failed,
        "cancelled": cancelled,
        "terminal": terminal,
        "progress": progress,
        "estimated_cost_usd": estimated_cost,
        "budgeted_cost_usd": budgeted_cost,
        "tokens": tokens,
        "unknown_cost_attempts": unknown_attempts,
        "cost_complete": unknown_attempts == 0
        and all(
            launch.cost_summary.get("cost_complete", True)
            for launch in launches
            if launch.cost_summary
        ),
        "proposed_fields": proposed_fields,
        "projectable_fields": projectable_fields,
        "planned_fields": planned_fields,
        "field_coverage_percent": (
            round(proposed_fields * 100 / planned_fields) if planned_fields else 0
        ),
        "normalized_values": normalized_values,
        "selected_documents": selected_documents,
        "parsed_documents": parsed_documents,
        "claims": claims,
        "accepted_claims": accepted_claims,
        "needs_review_claims": needs_review_claims,
        "rejected_claims": rejected_claims,
        "auto_finalized": auto_finalized,
        "published_fields": published_fields,
        "cost_per_proposed_field_usd": (
            estimated_cost / proposed_fields if proposed_fields else None
        ),
        "launches": launches,
    }


@transaction.atomic
def sync_campaign(campaign: FranchiseResearchCampaign) -> FranchiseResearchCampaign:
    """Derive campaign lifecycle from its authoritative child launches."""

    locked = FranchiseResearchCampaign.objects.select_for_update().get(pk=campaign.pk)
    statuses = list(locked.launches.values_list("status", flat=True))
    now = timezone.now()
    if not statuses:
        return locked
    running = FranchiseResearchLaunch.STATUS_RUNNING in statuses
    queued = FranchiseResearchLaunch.STATUS_QUEUED in statuses
    failed = FranchiseResearchLaunch.STATUS_FAILED in statuses
    succeeded = any(
        status in statuses
        for status in (
            FranchiseResearchLaunch.STATUS_SUCCEEDED,
            FranchiseResearchLaunch.STATUS_COMPLETE,
            FranchiseResearchLaunch.STATUS_PARTIAL,
            FranchiseResearchLaunch.STATUS_INSUFFICIENT,
        )
    )
    if running:
        status = FranchiseResearchCampaign.STATUS_RUNNING
    elif queued:
        status = FranchiseResearchCampaign.STATUS_QUEUED
    elif locked.cancel_requested:
        status = FranchiseResearchCampaign.STATUS_CANCELLED
    elif failed:
        status = FranchiseResearchCampaign.STATUS_COMPLETED_WITH_ERRORS
    elif succeeded:
        status = FranchiseResearchCampaign.STATUS_COMPLETED
    else:
        status = FranchiseResearchCampaign.STATUS_CANCELLED
    update_fields = []
    if locked.status != status:
        locked.status = status
        update_fields.append("status")
    if status == FranchiseResearchCampaign.STATUS_RUNNING and locked.started_at is None:
        locked.started_at = now
        update_fields.append("started_at")
    terminal_statuses = {
        FranchiseResearchCampaign.STATUS_COMPLETED,
        FranchiseResearchCampaign.STATUS_COMPLETED_WITH_ERRORS,
        FranchiseResearchCampaign.STATUS_CANCELLED,
    }
    if status in terminal_statuses and locked.completed_at is None:
        locked.completed_at = now
        update_fields.append("completed_at")
    elif status not in terminal_statuses and locked.completed_at is not None:
        locked.completed_at = None
        update_fields.append("completed_at")
    if update_fields:
        locked.save(update_fields=update_fields)
    return locked


@transaction.atomic
def create_research_campaign(
    *,
    name: str,
    description: str,
    franchises,
    profile_id: str,
    configuration: dict,
    max_total_cost_usd,
    max_concurrent_runs: int,
    include_previously_researched: bool,
    allow_inactive: bool = False,
    requested_by=None,
) -> FranchiseResearchCampaign:
    franchise_query = Franchise.objects.select_for_update().filter(
        pk__in=[item.pk for item in franchises]
    )
    if not allow_inactive:
        franchise_query = franchise_query.filter(is_active=True)
    franchises = list(franchise_query.order_by("name", "pk"))
    if not franchises:
        raise ResearchCampaignError(
            "Wybierz co najmniej jedną franczyzę dostępną dla tego typu kampanii."
        )
    if len(franchises) > 100:
        raise ResearchCampaignError("Jedna kampania może zawierać maksymalnie 100 franczyz.")
    if profile_id not in PROFILE_CHOICES:
        raise ResearchCampaignError("Nieobsługiwany profil researchu.")
    if not 1 <= max_concurrent_runs <= 5:
        raise ResearchCampaignError("Równolegle może działać od 1 do 5 runów.")
    active = FranchiseResearchLaunch.objects.filter(
        franchise__in=franchises,
        status__in=[
            FranchiseResearchLaunch.STATUS_QUEUED,
            FranchiseResearchLaunch.STATUS_RUNNING,
        ],
    ).select_related("franchise")
    if active.exists():
        names = ", ".join(active.values_list("franchise__name", flat=True)[:8])
        raise ResearchCampaignError(
            f"Aktywny research już istnieje dla: {names}. Poczekaj lub anuluj go."
        )
    if not include_previously_researched:
        previous = [
            franchise.name
            for franchise in franchises
            if franchise.research_workspaces.exists() or franchise.research_imports.exists()
        ]
        if previous:
            names = ", ".join(previous[:8])
            suffix = "…" if len(previous) > 8 else ""
            raise ResearchCampaignError(
                "Istniejący research wykryto dla: "
                f"{names}{suffix}. Włącz świadome ponowienie researchu albo usuń te pozycje."
            )
    per_run_cost = _decimal(configuration.get("max_cost_usd"))
    if per_run_cost <= 0:
        raise ResearchCampaignError("Budżet pojedynczego runu musi być dodatni.")
    reserved_cost = per_run_cost * len(franchises)
    max_total_cost = _decimal(max_total_cost_usd)
    if max_total_cost < reserved_cost:
        raise ResearchCampaignError(
            f"Budżet kampanii musi wynosić co najmniej ${reserved_cost:.2f} "
            "dla wybranej liczby franczyz."
        )
    campaign = FranchiseResearchCampaign.objects.create(
        name=name.strip(),
        description=description.strip(),
        target_country="PL",
        profile_id=profile_id,
        configuration=configuration,
        max_total_cost_usd=max_total_cost,
        reserved_cost_usd=reserved_cost,
        max_concurrent_runs=max_concurrent_runs,
        requested_by=requested_by,
    )
    for position, franchise in enumerate(franchises, start=1):
        queue_research_launch(
            franchise,
            profile_id=profile_id,
            known_legal_name="",
            # A directory URL is only an unverified retrieval seed. Searcher
            # and Human Review must validate it before it becomes official.
            known_official_website=franchise.website_url,
            configuration=configuration,
            requested_by=requested_by,
            campaign=campaign,
            campaign_position=position,
        )
    return campaign


@transaction.atomic
def cancel_research_campaign(campaign: FranchiseResearchCampaign) -> int:
    locked = FranchiseResearchCampaign.objects.select_for_update().get(pk=campaign.pk)
    if not locked.is_active:
        raise ResearchCampaignError("Tylko aktywną kampanię można zatrzymać.")
    locked.cancel_requested = True
    locked.save(update_fields=["cancel_requested"])
    cancelled = 0
    for launch in locked.launches.select_for_update().filter(
        status=FranchiseResearchLaunch.STATUS_QUEUED
    ):
        cancel_research_launch(launch)
        cancelled += 1
    sync_campaign(locked)
    return cancelled


@transaction.atomic
def retry_failed_campaign_launches(campaign: FranchiseResearchCampaign) -> int:
    locked = FranchiseResearchCampaign.objects.select_for_update().get(pk=campaign.pk)
    if locked.launches.filter(
        status__in=[
            FranchiseResearchLaunch.STATUS_QUEUED,
            FranchiseResearchLaunch.STATUS_RUNNING,
        ]
    ).exists():
        raise ResearchCampaignError("Poczekaj na zakończenie aktywnych pozycji kampanii.")
    failures = list(
        locked.launches.select_for_update().filter(
            status=FranchiseResearchLaunch.STATUS_FAILED
        )
    )
    if not failures:
        raise ResearchCampaignError("Kampania nie ma pozycji zakończonych błędem.")
    locked.cancel_requested = False
    locked.completed_at = None
    locked.save(update_fields=["cancel_requested", "completed_at"])
    for launch in failures:
        retry_research_launch(launch)
    sync_campaign(locked)
    return len(failures)


def campaign_launches_ready_for_claim():
    """Base candidate set; campaign concurrency is enforced under row locks."""

    return FranchiseResearchLaunch.objects.filter(
        status=FranchiseResearchLaunch.STATUS_QUEUED,
    ).filter(Q(campaign__isnull=True) | Q(campaign__cancel_requested=False))
