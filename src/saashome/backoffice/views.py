from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render

from billing.models import OrganizationSubscription, Plan

from .forms import SalesActivityForm, SalesOpportunityStageForm
from .models import SalesOpportunity
from .services.revenue import (
    get_cancelled_subscriptions, get_monthly_revenue_forecast, get_recent_revenue_events,
    get_retention_table, get_revenue_overview, get_subscription_rows, get_subscription_status_breakdown,
    get_top_customers_by_mrr,
)
from .services.sales import (
    add_sales_activity, change_opportunity_stage, get_opportunity_pipeline, get_overdue_followups,
    get_sales_dashboard, get_stale_opportunities,
)


def internal_context(**kwargs):
    context = {"site_name": "Porownaj Franczyze", "active_page": "backoffice", "robots_meta": "noindex,nofollow"}
    context.update(kwargs)
    return context


@staff_member_required
def internal_home_view(request):
    return render(request, "backoffice/internal_home.html", internal_context(page_title="Backoffice", revenue=get_revenue_overview(), sales=get_sales_dashboard()))


@staff_member_required
def internal_revenue_dashboard_view(request):
    retention_rows, retention_warning = get_retention_table()
    forecast_rows = get_monthly_revenue_forecast()
    forecast_chart = [
        {
            "month": row["month"].strftime("%Y-%m"),
            "expected_mrr": float(row["expected_mrr"]),
            "expected_cash_renewals": float(row["expected_cash_renewals"]),
            "subscriptions_ending_count": row["subscriptions_ending_count"],
        }
        for row in forecast_rows
    ]
    return render(request, "backoffice/revenue_dashboard.html", internal_context(
        page_title="Owner revenue dashboard",
        overview=get_revenue_overview(), forecast_rows=forecast_rows, forecast_chart=forecast_chart, retention_rows=retention_rows,
        retention_warning=retention_warning, status_breakdown=get_subscription_status_breakdown(),
        recent_revenue_events=get_recent_revenue_events(), cancelled_subscriptions=get_cancelled_subscriptions(),
        top_customers=get_top_customers_by_mrr(),
    ))


@staff_member_required
def internal_subscriptions_view(request):
    filters = {key: request.GET.get(key, "").strip() for key in ("status", "plan", "billing_interval", "cancelled")}
    rows = get_subscription_rows()
    rows = [row for row in rows if (not filters["status"] or row["subscription"].status == filters["status"]) and (not filters["plan"] or str(row["subscription"].plan_id) == filters["plan"]) and (not filters["billing_interval"] or row["subscription"].billing_interval == filters["billing_interval"]) and (filters["cancelled"] != "true" or row["subscription"].status in (OrganizationSubscription.STATUS_CANCELLED, OrganizationSubscription.STATUS_EXPIRED))]
    return render(request, "backoffice/subscriptions.html", internal_context(page_title="Subscriptions", subscription_rows=rows, filters=filters, plans=Plan.objects.order_by("sort_order", "name"), status_choices=OrganizationSubscription.STATUS_CHOICES))


@staff_member_required
def internal_sales_dashboard_view(request):
    filters = {key: request.GET.get(key, "").strip() for key in ("assigned_to", "stage", "overdue")}
    overview = get_sales_dashboard()
    return render(request, "backoffice/sales_dashboard.html", internal_context(
        page_title="Sales dashboard", overview=overview, pipeline_rows=get_opportunity_pipeline(filters),
        opportunities_by_stage=overview["opportunities_by_stage"], opportunities_by_salesperson=overview["opportunities_by_salesperson"],
        overdue_followups=get_overdue_followups()[:10], stale_opportunities=get_stale_opportunities()[:10], recent_activities=overview["recent_activities"],
        filters=filters, stage_choices=SalesOpportunity.STAGE_CHOICES,
    ))


@staff_member_required
def internal_sales_opportunity_detail_view(request, pk):
    opportunity = get_object_or_404(SalesOpportunity.objects.select_related("account", "organization", "franchise", "assigned_to").prefetch_related("account__contacts", "activities__contact", "activities__created_by"), pk=pk)
    activity_form = SalesActivityForm(opportunity=opportunity)
    stage_form = SalesOpportunityStageForm(instance=opportunity)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_activity":
            activity_form = SalesActivityForm(request.POST, opportunity=opportunity)
            if activity_form.is_valid():
                data = activity_form.cleaned_data
                add_sales_activity(opportunity, created_by=request.user, **data)
                messages.success(request, "Aktywność została dodana.")
                return redirect("backoffice:sales_opportunity_detail", pk=opportunity.pk)
        elif action == "change_stage":
            stage_form = SalesOpportunityStageForm(request.POST, instance=opportunity)
            if stage_form.is_valid():
                data = stage_form.cleaned_data
                new_stage = data.pop("stage")
                try:
                    change_opportunity_stage(opportunity, new_stage, user=request.user, **data)
                except ValueError as exc:
                    stage_form.add_error(None, str(exc))
                else:
                    messages.success(request, "Szansa sprzedażowa została zaktualizowana.")
                    return redirect("backoffice:sales_opportunity_detail", pk=opportunity.pk)
    return render(request, "backoffice/sales_opportunity_detail.html", internal_context(page_title=opportunity.title, opportunity=opportunity, contacts=opportunity.account.contacts.all(), activities=opportunity.activities.select_related("contact", "created_by"), activity_form=activity_form, stage_form=stage_form))
