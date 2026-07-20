from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from backoffice.models import SalesActivity, SalesOpportunity


CLOSED_STAGES = (SalesOpportunity.STAGE_WON, SalesOpportunity.STAGE_LOST, SalesOpportunity.STAGE_CHURNED)


def get_opportunity_pipeline(filters=None):
    filters = filters or {}
    queryset = SalesOpportunity.objects.select_related("account", "organization", "franchise", "assigned_to")
    if filters.get("assigned_to"):
        queryset = queryset.filter(assigned_to_id=filters["assigned_to"])
    if filters.get("stage"):
        queryset = queryset.filter(stage=filters["stage"])
    if filters.get("overdue"):
        queryset = queryset.filter(next_follow_up_at__lt=timezone.now()).exclude(stage__in=CLOSED_STAGES)
    return queryset


def get_overdue_followups():
    return SalesOpportunity.objects.select_related("account", "assigned_to").filter(next_follow_up_at__lt=timezone.now()).exclude(stage__in=CLOSED_STAGES).order_by("next_follow_up_at")


def get_stale_opportunities(days=14):
    threshold = timezone.now() - timedelta(days=days)
    return SalesOpportunity.objects.select_related("account", "assigned_to").filter(last_activity_at__lt=threshold).exclude(stage__in=CLOSED_STAGES) | SalesOpportunity.objects.select_related("account", "assigned_to").filter(last_activity_at__isnull=True).exclude(stage__in=CLOSED_STAGES)


def get_salesperson_performance():
    return list(SalesOpportunity.objects.exclude(assigned_to__isnull=True).values("assigned_to__username").annotate(count=Count("id"), expected_mrr=Sum("expected_monthly_value")).order_by("-expected_mrr"))


def get_sales_dashboard():
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    open_opportunities = SalesOpportunity.objects.exclude(stage__in=CLOSED_STAGES)
    pipeline_expected = sum((opportunity.expected_monthly_value for opportunity in open_opportunities), Decimal("0"))
    weighted_pipeline = sum((opportunity.weighted_monthly_value for opportunity in open_opportunities), Decimal("0"))
    return {
        "open_opportunities_count": open_opportunities.count(),
        "pipeline_expected_mrr": pipeline_expected,
        "weighted_pipeline_mrr": weighted_pipeline,
        "won_mrr_this_month": sum((opportunity.expected_monthly_value for opportunity in SalesOpportunity.objects.filter(stage=SalesOpportunity.STAGE_WON, won_at__gte=month_start)), Decimal("0")),
        "lost_opportunities_this_month": SalesOpportunity.objects.filter(stage=SalesOpportunity.STAGE_LOST, lost_at__gte=month_start).count(),
        "overdue_followups_count": get_overdue_followups().count(),
        "stale_opportunities_count": get_stale_opportunities().count(),
        "opportunities_by_stage": list(SalesOpportunity.objects.values("stage").annotate(count=Count("id"), expected_mrr=Sum("expected_monthly_value")).order_by("stage")),
        "opportunities_by_salesperson": get_salesperson_performance(),
        "recent_activities": SalesActivity.objects.select_related("account", "opportunity", "created_by")[:12],
    }


def add_sales_activity(opportunity, activity_type, subject="", body="", contact=None, due_at=None, completed_at=None, created_by=None):
    activity = SalesActivity.objects.create(account=opportunity.account, opportunity=opportunity, contact=contact, activity_type=activity_type, subject=subject, body=body, due_at=due_at, completed_at=completed_at, created_by=created_by)
    now = timezone.now()
    opportunity.last_activity_at = now
    if activity_type == SalesActivity.TYPE_TASK and due_at:
        opportunity.next_follow_up_at = due_at
    opportunity.save(update_fields=["last_activity_at", "next_follow_up_at", "updated_at"])
    opportunity.account.last_activity_at = now
    opportunity.account.save(update_fields=["last_activity_at", "updated_at"])
    return activity


def change_opportunity_stage(opportunity, new_stage, user=None, note="", **fields):
    if new_stage == SalesOpportunity.STAGE_LOST and not fields.get("lost_reason"):
        raise ValueError("Podaj powód utraty szansy.")
    if new_stage == SalesOpportunity.STAGE_CHURNED and not fields.get("churn_reason"):
        raise ValueError("Podaj powód churnu.")
    old_stage = opportunity.stage
    for field_name, value in fields.items():
        if hasattr(opportunity, field_name):
            setattr(opportunity, field_name, value)
    opportunity.stage = new_stage
    now = timezone.now()
    opportunity.last_activity_at = now
    if new_stage == SalesOpportunity.STAGE_WON:
        opportunity.won_at = now
    elif new_stage == SalesOpportunity.STAGE_LOST:
        opportunity.lost_at = now
    elif new_stage == SalesOpportunity.STAGE_CHURNED:
        opportunity.churned_at = now
    opportunity.save()
    SalesActivity.objects.create(account=opportunity.account, opportunity=opportunity, activity_type=SalesActivity.TYPE_STATUS_CHANGE, subject="Zmiana etapu", body=note, old_stage=old_stage, new_stage=new_stage, created_by=user)
    opportunity.account.last_activity_at = now
    opportunity.account.save(update_fields=["last_activity_at", "updated_at"])
    return opportunity
