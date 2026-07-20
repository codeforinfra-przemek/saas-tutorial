from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from billing.models import OrganizationSubscription

from backoffice.models import RevenueEvent


ACTIVE_STATUSES = (OrganizationSubscription.STATUS_ACTIVE, OrganizationSubscription.STATUS_TRIAL)
ZERO = Decimal("0")


def get_subscription_mrr(subscription):
    now = timezone.now()
    if subscription.status not in ACTIVE_STATUSES:
        return ZERO
    if subscription.starts_at and subscription.starts_at > now:
        return ZERO
    if subscription.ends_at and subscription.ends_at < now:
        return ZERO

    plan = subscription.plan
    interval = subscription.billing_interval or "monthly"
    if interval == "yearly":
        return (plan.price_yearly or ZERO) / Decimal("12")
    if interval == "monthly":
        return plan.price_monthly or ZERO
    stripe_price_id = getattr(subscription, "stripe_price_id", "")
    if stripe_price_id and stripe_price_id == getattr(plan, "stripe_price_yearly_id", ""):
        return (plan.price_yearly or ZERO) / Decimal("12")
    if stripe_price_id and stripe_price_id == getattr(plan, "stripe_price_monthly_id", ""):
        return plan.price_monthly or ZERO
    return plan.price_monthly or ZERO


def get_active_subscriptions():
    now = timezone.now()
    return (
        OrganizationSubscription.objects.select_related("organization", "plan")
        .filter(status__in=ACTIVE_STATUSES, starts_at__lte=now)
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
    )


def _month_start(value=None):
    value = value or timezone.localdate()
    return value.replace(day=1)


def get_revenue_overview():
    subscriptions = list(get_active_subscriptions())
    mrr = sum((get_subscription_mrr(subscription) for subscription in subscriptions), ZERO)
    month_start = _month_start()
    event_rows = RevenueEvent.objects.filter(effective_at__date__gte=month_start)
    positive_types = (RevenueEvent.EVENT_NEW_SUBSCRIPTION, RevenueEvent.EVENT_REACTIVATION, RevenueEvent.EVENT_UPGRADE)
    churn_types = (RevenueEvent.EVENT_CHURN, RevenueEvent.EVENT_CANCELLATION, RevenueEvent.EVENT_DOWNGRADE)
    new_mrr = sum((event.mrr_delta for event in event_rows.filter(event_type__in=positive_types) if event.mrr_delta > 0), ZERO)
    churned_mrr = sum((abs(event.mrr_delta) for event in event_rows.filter(event_type__in=churn_types) if event.mrr_delta < 0), ZERO)
    net_mrr = sum((event.mrr_delta for event in event_rows), ZERO)
    organizations = {subscription.organization_id for subscription in subscriptions if get_subscription_mrr(subscription) > ZERO}
    monthly_count = sum(1 for subscription in subscriptions if subscription.billing_interval == "monthly")
    yearly_count = sum(1 for subscription in subscriptions if subscription.billing_interval == "yearly")
    return {
        "mrr": mrr,
        "arr": mrr * Decimal("12"),
        "active_subscriptions_count": len(subscriptions),
        "active_paying_organizations_count": len(organizations),
        "monthly_subscriptions_count": monthly_count,
        "yearly_subscriptions_count": yearly_count,
        "new_mrr_this_month": new_mrr,
        "churned_mrr_this_month": churned_mrr,
        "net_mrr_movement_this_month": net_mrr,
        "average_revenue_per_account": mrr / Decimal(len(organizations)) if organizations else ZERO,
    }


def _next_month(value):
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def get_monthly_revenue_forecast(months=12):
    subscriptions = list(get_active_subscriptions())
    month = _month_start()
    rows = []
    for _ in range(months):
        next_month = _next_month(month)
        expected_mrr = ZERO
        cash_renewals = ZERO
        ending_count = 0
        for subscription in subscriptions:
            end_date = (subscription.current_period_end if hasattr(subscription, "current_period_end") else None) or subscription.ends_at
            if end_date and end_date.date() < month:
                continue
            expected_mrr += get_subscription_mrr(subscription)
            if end_date and month <= end_date.date() < next_month:
                ending_count += 1
                if subscription.billing_interval == "yearly":
                    cash_renewals += subscription.plan.price_yearly or ZERO
        rows.append({"month": month, "expected_mrr": expected_mrr, "expected_cash_renewals": cash_renewals, "subscriptions_ending_count": ending_count})
        month = next_month
    return rows


def get_retention_table(months=12):
    events = list(RevenueEvent.objects.filter(event_type__in=(RevenueEvent.EVENT_NEW_SUBSCRIPTION, RevenueEvent.EVENT_CHURN, RevenueEvent.EVENT_CANCELLATION)).order_by("effective_at"))
    if not events:
        return [], "Brak historii RevenueEvent. Uruchom backfill lub dodaj zdarzenia ręcznie."
    cohorts = {}
    for event in events:
        if event.event_type == RevenueEvent.EVENT_NEW_SUBSCRIPTION:
            cohorts.setdefault(event.organization_id, event)
    if not cohorts:
        return [], "Brak zdarzeń nowych subskrypcji do wyliczenia kohort."
    cutoff = _month_start()
    rows = []
    for cohort_month in sorted({_month_start(event.effective_at.date()) for event in cohorts.values()}, reverse=True)[:months]:
        starts = [event for event in cohorts.values() if _month_start(event.effective_at.date()) == cohort_month]
        start_ids = {event.organization_id for event in starts}
        churned_ids = {
            event.organization_id for event in events
            if event.organization_id in start_ids and event.event_type in (RevenueEvent.EVENT_CHURN, RevenueEvent.EVENT_CANCELLATION) and event.effective_at.date() >= cohort_month
        }
        mrr_start = sum((event.mrr_delta for event in starts), ZERO)
        mrr_churned = sum((abs(event.mrr_delta) for event in events if event.organization_id in start_ids and event.event_type in (RevenueEvent.EVENT_CHURN, RevenueEvent.EVENT_CANCELLATION) and event.mrr_delta < 0), ZERO)
        retained = len(start_ids - churned_ids)
        mrr_retained = max(ZERO, mrr_start - mrr_churned)
        rows.append({
            "cohort_month": cohort_month,
            "customers_start": len(start_ids),
            "customers_retained": retained,
            "customers_churned": len(churned_ids),
            "logo_retention_rate": (Decimal(retained) / Decimal(len(start_ids)) * 100) if start_ids else ZERO,
            "mrr_start": mrr_start,
            "mrr_retained": mrr_retained,
            "gross_revenue_retention_rate": (mrr_retained / mrr_start * 100) if mrr_start else ZERO,
        })
    return rows, ""


def get_subscription_status_breakdown():
    rows = defaultdict(int)
    for subscription in OrganizationSubscription.objects.values("status", "billing_interval"):
        rows[(subscription["status"], subscription["billing_interval"] or "monthly")] += 1
    return [{"status": status, "billing_interval": interval, "count": count} for (status, interval), count in rows.items()]


def get_subscription_rows():
    return [
        {"subscription": subscription, "mrr": get_subscription_mrr(subscription), "arr": get_subscription_mrr(subscription) * Decimal("12")}
        for subscription in OrganizationSubscription.objects.select_related("organization", "plan").all()
    ]


def get_recent_revenue_events(limit=20):
    return RevenueEvent.objects.select_related("organization", "plan", "subscription")[:limit]


def get_cancelled_subscriptions(limit=20):
    return OrganizationSubscription.objects.select_related("organization", "plan").filter(status__in=(OrganizationSubscription.STATUS_CANCELLED, OrganizationSubscription.STATUS_EXPIRED)).order_by("-updated_at")[:limit]


def get_top_customers_by_mrr(limit=10):
    rows = defaultdict(lambda: {"mrr": ZERO, "organization": None})
    for subscription in get_active_subscriptions():
        row = rows[subscription.organization_id]
        row["organization"] = subscription.organization
        row["mrr"] += get_subscription_mrr(subscription)
    return sorted(rows.values(), key=lambda row: row["mrr"], reverse=True)[:limit]
