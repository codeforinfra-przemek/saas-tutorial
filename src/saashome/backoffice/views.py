from django.contrib import messages
import hashlib
import json

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from billing.models import OrganizationSubscription, Plan
from franchises.forms import (
    ResearchCampaignForm,
    ResearchDocumentUploadForm,
    ResearchJobForm,
    ResearchLaunchForm,
    ResearchReviewFieldForm,
    ResearchWorkspaceDecisionForm,
)
from franchises.models import (
    FranchiseResearchCampaign,
    FranchiseResearchDocument,
    FranchiseResearchEvent,
    FranchiseResearchFinalization,
    FranchiseResearchJob,
    FranchiseResearchLaunch,
    FranchiseResearchReviewField,
    FranchiseResearchWorkspace,
)
from franchises.research_campaigns import (
    ResearchCampaignError,
    campaign_snapshot,
    cancel_research_campaign,
    create_research_campaign,
    retry_failed_campaign_launches,
)
from franchises.research_jobs import (
    ResearchJobError,
    cancel_queued_job,
    queue_research_job,
)
from franchises.research_fields import field_metadata, profile_info
from franchises.research_launches import (
    ResearchLaunchError,
    cancel_research_launch,
    queue_research_launch,
    retry_research_launch,
)

from .forms import (
    BenchmarkCampaignForm,
    BenchmarkGoldFieldForm,
    BenchmarkGoldPromotionForm,
    BenchmarkMetricsForm,
    BenchmarkSubmissionFieldForm,
    SalesActivityForm,
    SalesOpportunityStageForm,
)
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
from franchises.research_benchmark import (
    ResearchBenchmarkError,
    benchmark_brand,
    benchmark_campaign_scope,
    benchmark_dashboard,
    benchmark_gold_brand,
    benchmark_gold_dashboard,
    benchmark_paths,
    eligible_campaigns,
    export_campaign_submission,
    update_gold_field,
    update_submission_field,
    update_submission_metrics,
)
from franchises.research_gold_promotion import (
    GoldPromotionError,
    gold_promotion_preview,
    promote_gold_to_workspace,
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


def _workspace_counts(workspace):
    counts = {
        item["decision"]: item["count"]
        for item in workspace.review_fields.values("decision").annotate(count=Count("id"))
    }
    total = sum(counts.values())
    pending = counts.get(FranchiseResearchReviewField.DECISION_PENDING, 0)
    return {
        "total": total,
        "pending": pending,
        "reviewed": total - pending,
        "accepted": counts.get(FranchiseResearchReviewField.DECISION_ACCEPTED, 0),
        "edited": counts.get(FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED, 0),
        "policy_accepted": counts.get(
            FranchiseResearchReviewField.DECISION_POLICY_ACCEPTED, 0
        ),
        "rejected": counts.get(FranchiseResearchReviewField.DECISION_REJECTED, 0),
        "gaps": counts.get(FranchiseResearchReviewField.DECISION_DOCUMENTED_GAP, 0),
        "progress": round((total - pending) * 100 / total) if total else 0,
    }


@staff_member_required
def research_workbench_list_view(request):
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    workspaces = FranchiseResearchWorkspace.objects.select_related(
        "franchise", "reviewed_by"
    ).annotate(
        review_field_count=Count("review_fields", distinct=True),
        pending_field_count=Count(
            "review_fields",
            filter=Q(review_fields__decision=FranchiseResearchReviewField.DECISION_PENDING),
            distinct=True,
        ),
        reviewed_field_count=Count(
            "review_fields",
            filter=~Q(review_fields__decision=FranchiseResearchReviewField.DECISION_PENDING),
            distinct=True,
        ),
        document_count=Count("documents", distinct=True),
    )
    if q:
        workspaces = workspaces.filter(
            Q(franchise__name__icontains=q) | Q(profile_id__icontains=q)
        )
    valid_statuses = {item[0] for item in FranchiseResearchWorkspace.STATUS_CHOICES}
    if status in valid_statuses:
        workspaces = workspaces.filter(status=status)
    campaigns = list(
        FranchiseResearchCampaign.objects.select_related("requested_by").prefetch_related(
            "launches__franchise"
        )[:6]
    )
    for campaign in campaigns:
        campaign.snapshot = campaign_snapshot(campaign)
    return render(
        request,
        "backoffice/research_workbench_list.html",
        internal_context(
            page_title="Human Research Workbench",
            workspaces=workspaces,
            filters={"q": q, "status": status},
            status_choices=FranchiseResearchWorkspace.STATUS_CHOICES,
            launches=FranchiseResearchLaunch.objects.select_related(
                "franchise", "requested_by", "result_workspace"
            )[:10],
            campaigns=campaigns,
        ),
    )


@staff_member_required
def research_campaign_list_view(request):
    campaigns = list(
        FranchiseResearchCampaign.objects.select_related("requested_by").prefetch_related(
            "launches__franchise"
        )
    )
    for campaign in campaigns:
        campaign.snapshot = campaign_snapshot(campaign)
    return render(
        request,
        "backoffice/research_campaign_list.html",
        internal_context(page_title="Batch Campaigns", campaigns=campaigns),
    )


@staff_member_required
def research_benchmark_view(request):
    try:
        dashboard = benchmark_dashboard()
    except ResearchBenchmarkError as exc:
        messages.error(request, str(exc))
        dashboard = None
    scope = benchmark_campaign_scope()
    return render(
        request,
        "backoffice/research_benchmark.html",
        internal_context(
            page_title="PL:L1 Benchmark Workbench",
            dashboard=dashboard,
            campaigns=eligible_campaigns(),
            benchmark_scope=scope,
            benchmark_campaign_form=BenchmarkCampaignForm(
                brand_count=scope["total"]
            ),
        ),
    )


@staff_member_required
@require_POST
def research_benchmark_campaign_create_view(request):
    scope = benchmark_campaign_scope()
    form = BenchmarkCampaignForm(request.POST, brand_count=scope["total"])
    if not form.is_valid():
        messages.error(request, _benchmark_form_errors(form))
        return redirect("backoffice:research_benchmark")
    if scope["missing_slugs"]:
        messages.error(
            request,
            "Brakuje marek benchmarkowych w katalogu: "
            + ", ".join(scope["missing_slugs"]),
        )
        return redirect("backoffice:research_benchmark")
    if scope["busy_slugs"]:
        messages.error(
            request,
            "Aktywny research już działa dla: " + ", ".join(scope["busy_slugs"]),
        )
        return redirect("backoffice:research_benchmark")
    data = form.cleaned_data
    per_run_cost = data["max_cost_usd"]
    total_cost = per_run_cost * scope["total"]
    try:
        campaign = create_research_campaign(
            name=f"PL:L1 benchmark v1 — {timezone.localdate().isoformat()}",
            description=(
                "Kontrolowana kohorta 10 marek z benchmarku PL:L1. "
                "Nie publikować bez pełnego Human Review."
            ),
            franchises=scope["franchises"],
            profile_id="PL:L1",
            configuration={
                "max_cost_usd": str(per_run_cost),
                "initial_task_limit": 7,
                "max_search_calls": 10,
                "max_sources": 10,
                "max_extractor_api_calls": 15,
                "benchmark_spec_version": "1.0.0",
            },
            max_total_cost_usd=total_cost,
            max_concurrent_runs=data["max_concurrent_runs"],
            include_previously_researched=True,
            allow_inactive=True,
            requested_by=request.user,
        )
    except (ResearchCampaignError, ResearchLaunchError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("backoffice:research_benchmark")
    messages.success(
        request,
        f"Utworzono właściwą kampanię benchmarkową: {scope['total']} marek, "
        f"maksymalnie ${total_cost:.2f}.",
    )
    return redirect(
        "backoffice:research_campaign_detail",
        campaign_id=campaign.campaign_id,
    )


@staff_member_required
def research_benchmark_brand_view(request, brand_slug):
    try:
        context = benchmark_brand(brand_slug)
    except ResearchBenchmarkError as exc:
        raise Http404(str(exc)) from exc
    return render(
        request,
        "backoffice/research_benchmark_brand.html",
        internal_context(
            page_title=f"Benchmark: {context['definition'].name}",
            **context,
        ),
    )


@staff_member_required
def research_benchmark_gold_view(request):
    try:
        context = benchmark_gold_dashboard()
    except ResearchBenchmarkError as exc:
        messages.error(request, str(exc))
        context = None
    return render(
        request,
        "backoffice/research_benchmark_gold.html",
        internal_context(
            page_title="Zaślepiony Gold Set PL:L1",
            dashboard=context,
        ),
    )


@staff_member_required
def research_benchmark_gold_brand_view(request, brand_slug):
    try:
        context = benchmark_gold_brand(brand_slug)
    except ResearchBenchmarkError as exc:
        raise Http404(str(exc)) from exc
    return render(
        request,
        "backoffice/research_benchmark_gold_brand.html",
        internal_context(
            page_title=f"Gold Set: {context['definition'].name}",
            **context,
        ),
    )


@staff_member_required
def research_benchmark_gold_promote_view(request, brand_slug):
    workspace_id = (
        request.POST.get("workspace_id")
        if request.method == "POST"
        else request.GET.get("workspace")
    )
    try:
        context = gold_promotion_preview(brand_slug, workspace_id)
    except GoldPromotionError as exc:
        messages.error(request, str(exc))
        return redirect(
            "backoffice:research_benchmark_gold_brand",
            brand_slug=brand_slug,
        )

    if request.method == "POST":
        form = BenchmarkGoldPromotionForm(
            request.POST,
            promotion_rows=context["promotion_rows"],
        )
        if form.is_valid():
            try:
                result = promote_gold_to_workspace(
                    brand_slug,
                    workspace_id=form.cleaned_data["workspace_id"],
                    selected_field_ids=form.cleaned_data["selected_field_ids"],
                    expected_gold_sha256=form.cleaned_data["gold_sha256"],
                    actor=request.user,
                )
            except GoldPromotionError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    f"Przeniesiono {result['imported']} pól do Workbencha. "
                    "Pozostają oczekujące na Human Review.",
                )
                return redirect(
                    "backoffice:research_workbench_detail",
                    workspace_id=result["workspace"].workspace_id,
                )
        else:
            messages.error(request, _benchmark_form_errors(form))
    else:
        initial_ids = [
            str(row.review_field.pk)
            for row in context["promotion_rows"]
            if row.selected_by_default and row.review_field is not None
        ]
        form = BenchmarkGoldPromotionForm(
            promotion_rows=context["promotion_rows"],
            initial={
                "workspace_id": context["workspace"].workspace_id,
                "gold_sha256": context["gold_sha256"],
                "selected_field_ids": initial_ids,
            },
        )
    return render(
        request,
        "backoffice/research_benchmark_gold_promote.html",
        internal_context(
            page_title=f"Gold → Workbench: {context['definition'].name}",
            form=form,
            **context,
        ),
    )


def _benchmark_form_errors(form):
    return " ".join(
        f"{field}: {'; '.join(errors)}" for field, errors in form.errors.items()
    )


@staff_member_required
@require_POST
def research_benchmark_field_update_view(request, brand_slug, kind):
    if kind == "gold":
        form = BenchmarkGoldFieldForm(request.POST)
    elif kind in {"manual", "pipeline"}:
        form = BenchmarkSubmissionFieldForm(request.POST)
    else:
        raise Http404("Nieznany artefakt benchmarku.")
    if not form.is_valid():
        messages.error(request, _benchmark_form_errors(form))
    else:
        try:
            if kind == "gold":
                update_gold_field(
                    brand_slug,
                    form.cleaned_data["target_field"],
                    form.cleaned_data,
                )
            else:
                update_submission_field(
                    kind,
                    brand_slug,
                    form.cleaned_data["target_field"],
                    form.cleaned_data,
                )
        except ResearchBenchmarkError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Pole benchmarku zapisano i zwalidowano.")
    if kind == "gold" and request.POST.get("return_to") == "gold":
        return redirect(
            "backoffice:research_benchmark_gold_brand",
            brand_slug=brand_slug,
        )
    return redirect("backoffice:research_benchmark_brand", brand_slug=brand_slug)


@staff_member_required
@require_POST
def research_benchmark_metrics_update_view(request, brand_slug, kind):
    if kind not in {"manual", "pipeline"}:
        raise Http404("Nieznany artefakt benchmarku.")
    form = BenchmarkMetricsForm(request.POST)
    if not form.is_valid():
        messages.error(request, _benchmark_form_errors(form))
    else:
        try:
            update_submission_metrics(kind, brand_slug, form.cleaned_data)
        except ResearchBenchmarkError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Czas, zakres i koszt zapisano.")
    return redirect("backoffice:research_benchmark_brand", brand_slug=brand_slug)


@staff_member_required
@require_POST
def research_benchmark_export_view(request):
    campaign = get_object_or_404(
        FranchiseResearchCampaign, campaign_id=request.POST.get("campaign_id")
    )
    try:
        result = export_campaign_submission(
            campaign,
            exported_by=request.user.get_username(),
        )
    except ResearchBenchmarkError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Eksport zakończony: "
            f"{result['matched_brands']}/{result['benchmark_brands']} marek, "
            f"{result['ready_brands']} gotowych do oceny.",
        )
    return redirect("backoffice:research_benchmark")


@staff_member_required
def research_benchmark_download_view(request, artifact):
    if artifact in {"gold", "manual", "pipeline", "experiment"}:
        try:
            path = benchmark_paths()[artifact]
            payload = path.read_bytes()
        except (KeyError, OSError) as exc:
            raise Http404("Artefakt benchmarku nie istnieje.") from exc
        response = HttpResponse(payload, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{path.name}"'
        return response
    if artifact in {"manual-evaluation", "pipeline-evaluation"}:
        dashboard = benchmark_dashboard()
        key = artifact.removesuffix("-evaluation") + "_evaluation"
        payload = json.dumps(
            dashboard[key], ensure_ascii=False, indent=2
        ).encode("utf-8")
        response = HttpResponse(payload, content_type="application/json")
        response["Content-Disposition"] = f'attachment; filename="{artifact}.json"'
        return response
    raise Http404("Nieznany eksport benchmarku.")


@staff_member_required
def research_campaign_create_view(request):
    form = ResearchCampaignForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        configuration = {
            "max_cost_usd": str(data["max_cost_usd"]),
            "initial_task_limit": data["initial_task_limit"],
            "max_search_calls": data["max_search_calls"],
            "max_sources": data["max_sources"],
            "max_extractor_api_calls": data["max_extractor_api_calls"],
            "auto_review_finalize": data["auto_review_finalize"],
            "monitoring_gate": {
                "enabled": data["monitor_quality_gate"],
                "minimum_completed": data["gate_minimum_completed"] or 3,
                "minimum_average_proposals": str(
                    data["gate_minimum_average_proposals"] or 8
                ),
                "minimum_average_publications": str(
                    data["gate_minimum_average_publications"] or 5
                ),
                "stop_on_unknown_cost": True,
            },
        }
        try:
            campaign = create_research_campaign(
                name=data["name"],
                description=data["description"],
                franchises=data["franchises"],
                profile_id=data["profile_id"],
                configuration=configuration,
                max_total_cost_usd=data["max_total_cost_usd"],
                max_concurrent_runs=data["max_concurrent_runs"],
                include_previously_researched=data["include_previously_researched"],
                requested_by=request.user,
            )
        except (ResearchCampaignError, ResearchLaunchError, ValueError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(
                request,
                f"Kampania została utworzona. Do kolejki dodano {campaign.launches.count()} runów.",
            )
            return redirect(
                "backoffice:research_campaign_detail",
                campaign_id=campaign.campaign_id,
            )
    return render(
        request,
        "backoffice/research_campaign_form.html",
        internal_context(page_title="Nowa kampania", form=form),
    )


def _campaign_or_404(campaign_id):
    return get_object_or_404(
        FranchiseResearchCampaign.objects.select_related("requested_by").prefetch_related(
            "launches__franchise", "launches__result_workspace"
        ),
        campaign_id=campaign_id,
    )


@staff_member_required
def research_campaign_detail_view(request, campaign_id):
    campaign = _campaign_or_404(campaign_id)
    return render(
        request,
        "backoffice/research_campaign_detail.html",
        internal_context(
            page_title=f"Kampania: {campaign.name}",
            campaign=campaign,
            snapshot=campaign_snapshot(campaign),
            profile=profile_info(campaign.profile_id),
        ),
    )


@staff_member_required
def research_campaign_status_view(request, campaign_id):
    campaign = _campaign_or_404(campaign_id)
    snapshot = campaign_snapshot(campaign)
    launches = []
    for launch in snapshot["launches"]:
        launches.append(
            {
                "id": str(launch.launch_id),
                "franchise": launch.franchise.name,
                "status": launch.status,
                "status_label": launch.get_status_display(),
                "scope_label": launch.scope_label,
                "proposed_fields": launch.proposed_field_count,
                "projectable_fields": launch.projectable_field_count,
                "stage": launch.current_stage,
                "progress": launch.progress_percent,
                "cost": launch.cost_summary.get("estimated_cost_usd") or "0",
                "tokens": launch.cost_summary.get("total_tokens") or 0,
                "error": launch.error_message,
                "url": reverse(
                    "backoffice:research_launch_detail", args=[launch.launch_id]
                ),
                "workspace_url": reverse(
                    "backoffice:research_workbench_detail",
                    args=[launch.result_workspace.workspace_id],
                ) if launch.result_workspace_id else "",
            }
        )
    return JsonResponse(
        {
            "status": campaign.status,
            "status_label": campaign.get_status_display(),
            "is_active": campaign.is_active,
            "cancel_requested": campaign.cancel_requested,
            "progress": snapshot["progress"],
            "counts": {
                key: snapshot[key]
                for key in ("total", "queued", "running", "succeeded", "failed", "cancelled")
            },
            "estimated_cost_usd": str(snapshot["estimated_cost_usd"]),
            "budgeted_cost_usd": str(snapshot["budgeted_cost_usd"]),
            "tokens": snapshot["tokens"],
            "cost_complete": snapshot["cost_complete"],
            "unknown_cost_attempts": snapshot["unknown_cost_attempts"],
            "proposed_fields": snapshot["proposed_fields"],
            "projectable_fields": snapshot["projectable_fields"],
            "planned_fields": snapshot["planned_fields"],
            "field_coverage_percent": snapshot["field_coverage_percent"],
            "normalized_values": snapshot["normalized_values"],
            "selected_documents": snapshot["selected_documents"],
            "parsed_documents": snapshot["parsed_documents"],
            "claims": snapshot["claims"],
            "accepted_claims": snapshot["accepted_claims"],
            "needs_review_claims": snapshot["needs_review_claims"],
            "rejected_claims": snapshot["rejected_claims"],
            "cost_per_proposed_field_usd": (
                str(snapshot["cost_per_proposed_field_usd"])
                if snapshot["cost_per_proposed_field_usd"] is not None
                else None
            ),
            "monitoring": snapshot["monitoring"],
            "launches": launches,
        }
    )


@staff_member_required
@require_POST
def research_campaign_cancel_view(request, campaign_id):
    campaign = get_object_or_404(FranchiseResearchCampaign, campaign_id=campaign_id)
    try:
        cancelled = cancel_research_campaign(campaign)
    except ResearchCampaignError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Anulowano {cancelled} oczekujących runów. Uruchomione pozycje zakończą bieżący przebieg.",
        )
    return redirect("backoffice:research_campaign_detail", campaign_id=campaign.campaign_id)


@staff_member_required
@require_POST
def research_campaign_retry_view(request, campaign_id):
    campaign = get_object_or_404(FranchiseResearchCampaign, campaign_id=campaign_id)
    try:
        retried = retry_failed_campaign_launches(campaign)
    except (ResearchCampaignError, ResearchLaunchError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Ponownie zakolejkowano {retried} pozycji od ostatniego poprawnego artefaktu.",
        )
    return redirect("backoffice:research_campaign_detail", campaign_id=campaign.campaign_id)


@staff_member_required
def research_launch_create_view(request):
    form = ResearchLaunchForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        configuration = {
            "max_cost_usd": str(data["max_cost_usd"]),
            "initial_task_limit": data["initial_task_limit"],
            "max_search_calls": data["max_search_calls"],
            "max_sources": data["max_sources"],
            "max_extractor_api_calls": data["max_extractor_api_calls"],
            "auto_review_finalize": data["auto_review_finalize"],
        }
        try:
            launch = queue_research_launch(
                data["franchise"],
                profile_id=data["profile_id"],
                known_legal_name=data["known_legal_name"],
                known_official_website=data["known_official_website"],
                configuration=configuration,
                requested_by=request.user,
            )
        except (ResearchLaunchError, ValueError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(
                request,
                "Pierwszy run trafił do trwałej kolejki. Możesz zamknąć stronę.",
            )
            return redirect("backoffice:research_launch_detail", launch_id=launch.launch_id)
    return render(
        request,
        "backoffice/research_launch_form.html",
        internal_context(
            page_title="Nowy research",
            form=form,
        ),
    )


@staff_member_required
def research_launch_detail_view(request, launch_id):
    launch = get_object_or_404(
        FranchiseResearchLaunch.objects.select_related(
            "franchise", "requested_by", "result_workspace", "campaign"
        ),
        launch_id=launch_id,
    )
    return render(
        request,
        "backoffice/research_launch_detail.html",
        internal_context(
            page_title=f"Run: {launch.franchise.name}",
            launch=launch,
            profile=profile_info(launch.profile_id),
        ),
    )


@staff_member_required
def research_launch_status_view(request, launch_id):
    launch = get_object_or_404(
        FranchiseResearchLaunch.objects.select_related("result_workspace"),
        launch_id=launch_id,
    )
    result_url = (
        reverse(
            "backoffice:research_workbench_detail",
            args=[launch.result_workspace.workspace_id],
        )
        if launch.result_workspace_id
        else ""
    )
    return JsonResponse(
        {
            "status": launch.status,
            "status_label": launch.get_status_display(),
            "stage": launch.current_stage,
            "progress": launch.progress_percent,
            "cost": launch.cost_summary.get("estimated_cost_usd"),
            "cost_complete": launch.cost_summary.get("cost_complete", True),
            "budgeted_cost": launch.cost_summary.get("budgeted_cost_usd"),
            "unknown_cost_attempts": launch.cost_summary.get("unknown_cost_attempts", 0),
            "tokens": launch.cost_summary.get("total_tokens", 0),
            "error": launch.error_message,
            "log_tail": launch.log[-4000:],
            "result_url": result_url,
            "is_active": launch.is_active,
        }
    )


@staff_member_required
@require_POST
def research_launch_cancel_view(request, launch_id):
    launch = get_object_or_404(FranchiseResearchLaunch, launch_id=launch_id)
    try:
        cancel_research_launch(launch)
    except ResearchLaunchError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Run został anulowany przed uruchomieniem.")
    return redirect("backoffice:research_launch_detail", launch_id=launch.launch_id)


@staff_member_required
@require_POST
def research_launch_retry_view(request, launch_id):
    launch = get_object_or_404(FranchiseResearchLaunch, launch_id=launch_id)
    try:
        retry_research_launch(launch)
    except ResearchLaunchError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            "Run wznowiono od ostatniego poprawnie zweryfikowanego artefaktu.",
        )
    return redirect("backoffice:research_launch_detail", launch_id=launch.launch_id)


@staff_member_required
def research_workbench_detail_view(request, workspace_id):
    workspace = get_object_or_404(
        FranchiseResearchWorkspace.objects.select_related(
            "franchise", "created_by", "reviewed_by"
        ),
        workspace_id=workspace_id,
    )
    decision_filter = request.GET.get("decision", "").strip()
    data_filter = request.GET.get("data", "").strip()
    q = request.GET.get("q", "").strip()
    fields = workspace.review_fields.select_related("decided_by").prefetch_related(
        "supporting_documents"
    )
    valid_decisions = {item[0] for item in FranchiseResearchReviewField.DECISION_CHOICES}
    if decision_filter in valid_decisions:
        fields = fields.filter(decision=decision_filter)
    if data_filter == "with_values":
        fields = fields.exclude(proposed_values=[])
    elif data_filter == "missing":
        fields = fields.filter(proposed_values=[])
    elif data_filter == "attention":
        fields = fields.filter(
            Q(pipeline_status__in=["missing", "not_evaluated", "needs_review", "conflicting"])
            | Q(decision=FranchiseResearchReviewField.DECISION_PENDING)
        )
    if q:
        fields = fields.filter(
            Q(target_field__icontains=q)
            | Q(task_title__icontains=q)
            | Q(reviewer_value__icontains=q)
        )
    page_obj = Paginator(fields, 30).get_page(request.GET.get("page"))
    grouped_fields = []
    current_task_id = None
    current_group = None
    for field in page_obj.object_list:
        field.catalog_metadata = field_metadata(
            field.target_field,
            task_title=field.task_title,
        )
        if field.task_id != current_task_id:
            current_task_id = field.task_id
            current_group = {
                "task_id": field.task_id,
                "title": field.task_title,
                "fields": [],
            }
            grouped_fields.append(current_group)
        current_group["fields"].append(field)
    try:
        finalization = workspace.finalization
    except FranchiseResearchFinalization.DoesNotExist:
        finalization = None
    stages = []
    for stage in workspace.stage_summary:
        rendered = dict(stage)
        if rendered["key"] == "review":
            rendered["status"] = (
                "complete"
                if workspace.status
                in {
                    FranchiseResearchWorkspace.STATUS_READY,
                    FranchiseResearchWorkspace.STATUS_APPROVED,
                    FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS,
                }
                else "attention"
                if workspace.status == FranchiseResearchWorkspace.STATUS_REJECTED
                else "current"
            )
            rendered["summary"] = f"{_workspace_counts(workspace)['progress']}% sprawdzone"
        if rendered["key"] == "import" and workspace.status in {
            FranchiseResearchWorkspace.STATUS_APPROVED,
            FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS,
        }:
            rendered["status"] = "complete" if finalization else "current"
            rendered["summary"] = (
                "zamrożono i zaimportowano"
                if finalization
                else "gotowe do finalizacji i importu"
            )
        stages.append(rendered)
    jobs = workspace.jobs.select_related("requested_by", "result_workspace")[:10]
    active_job = workspace.jobs.filter(
        status__in=[
            FranchiseResearchJob.STATUS_QUEUED,
            FranchiseResearchJob.STATUS_RUNNING,
        ]
    ).first()
    return render(
        request,
        "backoffice/research_workbench_detail.html",
        internal_context(
            page_title=f"Research: {workspace.franchise.name}",
            workspace=workspace,
            stages=stages,
            grouped_fields=grouped_fields,
            page_obj=page_obj,
            counts=_workspace_counts(workspace),
            filters={"decision": decision_filter, "data": data_filter, "q": q},
            decision_choices=FranchiseResearchReviewField.DECISION_CHOICES,
            document_form=ResearchDocumentUploadForm(),
            workspace_form=ResearchWorkspaceDecisionForm(
                initial={"reviewer_notes": workspace.reviewer_notes}
            ),
            job_form=ResearchJobForm(),
            jobs=jobs,
            active_job=active_job,
            finalization=finalization,
            profile=profile_info(workspace.profile_id, workspace.depth),
            documents=workspace.documents.select_related("uploaded_by"),
            events=workspace.events.select_related("actor")[:20],
        ),
    )


def _reopen_workspace_if_needed(workspace):
    if workspace.is_finalized:
        raise ResearchFinalizationError(
            "Sfinalizowany Workbench jest niezmienny. Utwórz nowy draft."
        )
    if workspace.status != FranchiseResearchWorkspace.STATUS_REVIEW:
        workspace.status = FranchiseResearchWorkspace.STATUS_REVIEW
        workspace.reviewed_by = None
        workspace.reviewed_at = None
        workspace.auto_reviewed = False
        workspace.review_policy_version = ""
        workspace.auto_review_summary = {}
        workspace.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "auto_reviewed",
                "review_policy_version",
                "auto_review_summary",
                "updated_at",
            ]
        )


def _workbench_return(request, workspace_id, *, anchor=""):
    candidate = request.POST.get("return_to", "")
    if not url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        candidate = reverse("backoffice:research_workbench_detail", args=[workspace_id])
    if anchor:
        candidate = f"{candidate.split('#', 1)[0]}#{anchor}"
    return redirect(candidate)


def _wants_json(request):
    return (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or "application/json" in request.headers.get("accept", "")
    )


def _field_review_payload(field):
    return {
        "id": field.pk,
        "decision": field.decision,
        "decision_label": field.get_decision_display(),
        "effective_value": field.effective_value,
        "reviewer_value": field.reviewer_value,
        "reviewer_note": field.reviewer_note,
        "decided_by": field.decided_by.get_username() if field.decided_by else None,
        "decided_at": field.decided_at.isoformat() if field.decided_at else None,
        "updated_at": field.updated_at.isoformat(),
    }


def _workbench_mutation_error(request, workspace_id, message, *, anchor="", status=400):
    if _wants_json(request):
        return JsonResponse({"ok": False, "error": message}, status=status)
    messages.error(request, message)
    return _workbench_return(request, workspace_id, anchor=anchor)


def _field_review_json(workspace, field, event, message):
    return JsonResponse(
        {
            "ok": True,
            "message": message,
            "field": _field_review_payload(field),
            "counts": _workspace_counts(workspace),
            "event": {
                "message": event.message,
                "created_at": timezone.localtime(event.created_at).strftime("%d.%m.%Y %H:%M"),
                "actor": event.actor.get_username() if event.actor else None,
            },
        }
    )


def _field_version_conflict(request, workspace_id, field):
    supplied_version = request.POST.get("field_version", "").strip()
    if supplied_version and supplied_version != field.updated_at.isoformat():
        return _workbench_mutation_error(
            request,
            workspace_id,
            "To pole zostało już zmienione w innej karcie. Odśwież widok przed ponownym zapisem.",
            anchor=f"field-{field.pk}",
            status=409,
        )
    return None


@staff_member_required
@require_POST
def research_workbench_field_action_view(request, workspace_id, pk, action):
    decisions = {
        "accept": FranchiseResearchReviewField.DECISION_ACCEPTED,
        "reject": FranchiseResearchReviewField.DECISION_REJECTED,
        "gap": FranchiseResearchReviewField.DECISION_DOCUMENTED_GAP,
        "reset": FranchiseResearchReviewField.DECISION_PENDING,
    }
    if action not in decisions:
        raise Http404
    with transaction.atomic():
        workspace = get_object_or_404(
            FranchiseResearchWorkspace.objects.select_for_update(),
            workspace_id=workspace_id,
        )
        if workspace.is_finalized or workspace.status != FranchiseResearchWorkspace.STATUS_REVIEW:
            return _workbench_mutation_error(
                request,
                workspace_id,
                "Najpierw otwórz Workbench ponownie do edycji.",
                status=409,
            )
        field = get_object_or_404(
            FranchiseResearchReviewField.objects.select_for_update(),
            workspace=workspace,
            pk=pk,
        )
        conflict = _field_version_conflict(request, workspace_id, field)
        if conflict:
            return conflict
        if action == "accept" and not field.effective_value:
            return _workbench_mutation_error(
                request,
                workspace_id,
                "Najpierw wpisz wartość albo oznacz pole jako udokumentowany brak.",
                anchor=f"field-{field.pk}",
            )
        field.decision = (
            FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED
            if action == "accept" and field.reviewer_value.strip()
            else decisions[action]
        )
        if action == "reset":
            field.decided_by = None
            field.decided_at = None
        else:
            field.decided_by = request.user
            field.decided_at = timezone.now()
        field.save(update_fields=["decision", "decided_by", "decided_at", "updated_at"])
        event = FranchiseResearchEvent.objects.create(
            workspace=workspace,
            event_type="field_decision",
            message=f"{field.target_field}: {field.get_decision_display()}.",
            metadata={"field_id": field.pk, "decision": field.decision},
            actor=request.user,
        )
    message = f"Zapisano decyzję dla pola „{field.target_field}”."
    if _wants_json(request):
        return _field_review_json(workspace, field, event, message)
    messages.success(request, message)
    return _workbench_return(request, workspace_id, anchor=f"field-{field.pk}")


@staff_member_required
@require_POST
def research_workbench_field_edit_view(request, workspace_id, pk):
    with transaction.atomic():
        workspace = get_object_or_404(
            FranchiseResearchWorkspace.objects.select_for_update(),
            workspace_id=workspace_id,
        )
        if workspace.is_finalized or workspace.status != FranchiseResearchWorkspace.STATUS_REVIEW:
            return _workbench_mutation_error(
                request,
                workspace_id,
                "Najpierw otwórz Workbench ponownie do edycji.",
                status=409,
            )
        field = get_object_or_404(
            FranchiseResearchReviewField.objects.select_for_update(),
            workspace=workspace,
            pk=pk,
        )
        conflict = _field_version_conflict(request, workspace_id, field)
        if conflict:
            return conflict
        previous_document_ids = set(field.supporting_documents.values_list("id", flat=True))
        form = ResearchReviewFieldForm(request.POST, instance=field)
        if not form.is_valid():
            return _workbench_mutation_error(
                request,
                workspace_id,
                "Nie udało się zapisać korekty. Sprawdź wpisane dane.",
                anchor=f"field-{field.pk}",
            )
        if not form.cleaned_data["reviewer_value"].strip():
            return _workbench_mutation_error(
                request,
                workspace_id,
                "Wartość po korekcie nie może być pusta.",
                anchor=f"field-{field.pk}",
            )
        field = form.save(commit=False)
        field.decision = FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED
        field.decided_by = request.user
        field.decided_at = timezone.now()
        field.save()
        form.save_m2m()
        current_document_ids = set(field.supporting_documents.values_list("id", flat=True))
        impacted_document_ids = previous_document_ids | current_document_ids
        for document in workspace.documents.filter(id__in=impacted_document_ids):
            document.status = (
                FranchiseResearchDocument.STATUS_READY
                if document.supported_review_fields.exists()
                else FranchiseResearchDocument.STATUS_PENDING
            )
            document.save(update_fields=["status"])
        event = FranchiseResearchEvent.objects.create(
            workspace=workspace,
            event_type="field_edited",
            message=f"Uzupełniono i zatwierdzono pole {field.target_field}.",
            metadata={"field_id": field.pk},
            actor=request.user,
        )
    message = "Korekta została zapisana i zaakceptowana."
    if _wants_json(request):
        return _field_review_json(workspace, field, event, message)
    messages.success(request, message)
    return _workbench_return(request, workspace_id, anchor=f"field-{field.pk}")


@staff_member_required
@require_POST
def research_workbench_document_upload_view(request, workspace_id):
    workspace = get_object_or_404(FranchiseResearchWorkspace, workspace_id=workspace_id)
    if workspace.is_finalized or workspace.status != FranchiseResearchWorkspace.STATUS_REVIEW:
        messages.error(request, "Najpierw otwórz Workbench ponownie do edycji.")
        return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
    form = ResearchDocumentUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(
            request,
            "Nie udało się dodać dokumentu: "
            + " ".join(error for errors in form.errors.values() for error in errors),
        )
        return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
    uploaded = form.cleaned_data["file"]
    digest = hashlib.sha256()
    for chunk in uploaded.chunks():
        digest.update(chunk)
    uploaded.seek(0)
    if workspace.documents.filter(sha256=digest.hexdigest()).exists():
        messages.info(request, "Ten sam dokument jest już w tym Workbenchu.")
        return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
    document = form.save(commit=False)
    document.workspace = workspace
    document.original_name = uploaded.name[:255]
    document.content_type = getattr(uploaded, "content_type", "")[:120]
    document.size_bytes = uploaded.size
    document.sha256 = digest.hexdigest()
    document.uploaded_by = request.user
    try:
        document.save()
    except IntegrityError:
        messages.info(request, "Ten sam dokument jest już w tym Workbenchu.")
        return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
    FranchiseResearchEvent.objects.create(
        workspace=workspace,
        event_type="document_uploaded",
        message=f"Dodano dokument: {document.original_name}.",
        metadata={"document_id": document.pk, "access_level": document.access_level},
        actor=request.user,
    )
    messages.success(request, "Dokument dodano bezpiecznie. Czeka na analizę.")
    return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)


@staff_member_required
@require_POST
def research_workbench_job_queue_view(request, workspace_id):
    workspace = get_object_or_404(FranchiseResearchWorkspace, workspace_id=workspace_id)
    if not workspace.is_finalized and workspace.status != FranchiseResearchWorkspace.STATUS_REVIEW:
        messages.error(request, "Najpierw otwórz Workbench ponownie do edycji.")
        return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
    form = ResearchJobForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            "Nie udało się uruchomić researchu: "
            + " ".join(error for errors in form.errors.values() for error in errors),
        )
        return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
    data = form.cleaned_data
    configuration = {
        "policy": data["policy"],
        "max_cost_usd": str(data["max_cost_usd"]),
        "max_rounds": data["max_rounds"],
        "normalize_incomplete": data["normalize_incomplete"],
        "max_search_calls": data["max_search_calls"],
        "max_extractor_api_calls": data["max_extractor_api_calls"],
    }
    try:
        job = queue_research_job(
            workspace,
            kind=data["kind"],
            configuration=configuration,
            requested_by=request.user,
        )
    except (ResearchJobError, OSError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Zadanie „{job.get_kind_display()}” trafiło do kolejki. "
            "Postęp pojawi się w tym widoku.",
        )
    return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)


@staff_member_required
@require_POST
def research_workbench_job_cancel_view(request, workspace_id, job_id):
    job = get_object_or_404(
        FranchiseResearchJob.objects.select_related("workspace"),
        workspace__workspace_id=workspace_id,
        job_id=job_id,
    )
    try:
        cancel_queued_job(job, actor=request.user)
    except ResearchJobError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Zadanie zostało anulowane przed uruchomieniem.")
    return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)


@staff_member_required
def research_workbench_job_status_view(request, workspace_id, job_id):
    job = get_object_or_404(
        FranchiseResearchJob.objects.select_related("result_workspace"),
        workspace__workspace_id=workspace_id,
        job_id=job_id,
    )
    result_url = (
        reverse(
            "backoffice:research_workbench_detail",
            args=[job.result_workspace.workspace_id],
        )
        if job.result_workspace_id
        else reverse(
            "franchises:research_detail",
            args=[job.result_summary["franchise_slug"]],
        )
        if job.kind == FranchiseResearchJob.KIND_FINALIZE
        and job.status == FranchiseResearchJob.STATUS_SUCCEEDED
        and job.result_summary.get("franchise_slug")
        else ""
    )
    return JsonResponse(
        {
            "job_id": str(job.job_id),
            "status": job.status,
            "status_label": job.get_status_display(),
            "stage": job.current_stage,
            "progress": job.progress_percent,
            "cost": job.cost_summary.get("estimated_cost_usd"),
            "tokens": job.cost_summary.get("total_tokens", 0),
            "error": job.error_message,
            "log_tail": job.log[-4000:],
            "result_url": result_url,
            "is_active": job.is_active,
        }
    )


@staff_member_required
def research_workbench_document_download_view(request, workspace_id, pk):
    document = get_object_or_404(
        FranchiseResearchDocument,
        workspace__workspace_id=workspace_id,
        pk=pk,
    )
    try:
        handle = document.file.open("rb")
    except OSError as exc:
        raise Http404 from exc
    return FileResponse(
        handle,
        as_attachment=True,
        filename=document.original_name,
        content_type=document.content_type or "application/octet-stream",
    )


@staff_member_required
@require_POST
def research_workbench_decision_view(request, workspace_id, action):
    workspace = get_object_or_404(FranchiseResearchWorkspace, workspace_id=workspace_id)
    if workspace.is_finalized:
        messages.error(request, "Sfinalizowanej decyzji nie można zmienić.")
        return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
    form = ResearchWorkspaceDecisionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Nie udało się zapisać decyzji końcowej.")
        return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
    counts = _workspace_counts(workspace)
    if action in {"ready", "approve"}:
        if counts["pending"]:
            messages.error(
                request,
                f"Pozostało {counts['pending']} pól bez decyzji. Zatwierdź, odrzuć lub oznacz je jako brak.",
            )
            return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
        if counts["gaps"] or counts["rejected"]:
            messages.error(
                request,
                "Pełne zatwierdzenie nie może zawierać odrzuceń ani udokumentowanych braków.",
            )
            return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
        if not workspace.checker_passed or not workspace.scope_complete:
            messages.error(
                request,
                "Checker lub zakres nie pozwala na pełne zatwierdzenie. Użyj zatwierdzenia z brakami.",
            )
            return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
        status = FranchiseResearchWorkspace.STATUS_APPROVED
    elif action == "approve_with_gaps":
        if not form.cleaned_data["acknowledge_gaps"]:
            messages.error(request, "Potwierdź świadome zatwierdzenie udokumentowanych braków.")
            return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
        status = FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS
    elif action == "reject":
        status = FranchiseResearchWorkspace.STATUS_REJECTED
    elif action == "reopen":
        status = FranchiseResearchWorkspace.STATUS_REVIEW
    else:
        raise Http404
    workspace.status = status
    workspace.reviewer_notes = form.cleaned_data["reviewer_notes"]
    workspace.reviewed_by = request.user if action != "reopen" else None
    workspace.reviewed_at = timezone.now() if action != "reopen" else None
    workspace.save(
        update_fields=[
            "status",
            "reviewer_notes",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ]
    )
    FranchiseResearchEvent.objects.create(
        workspace=workspace,
        event_type="workspace_decision",
        message=f"Status Workbencha: {workspace.get_status_display()}.",
        metadata={"status": workspace.status, "pending_fields": counts["pending"]},
        actor=request.user,
    )
    messages.success(request, f"Zapisano status: {workspace.get_status_display()}.")
    return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)


@staff_member_required
@require_POST
def research_workbench_finalize_view(request, workspace_id):
    workspace = get_object_or_404(FranchiseResearchWorkspace, workspace_id=workspace_id)
    if workspace.is_finalized:
        return redirect(
            "franchises:research_detail",
            slug=workspace.franchise.slug,
        )
    try:
        job = queue_research_job(
            workspace,
            kind=FranchiseResearchJob.KIND_FINALIZE,
            configuration={},
            requested_by=request.user,
        )
    except (ResearchJobError, OSError, ValueError) as exc:
        messages.error(request, f"Nie udało się zakolejkować finalizacji: {exc}")
    else:
        messages.success(
            request,
            f"Finalizacja trafiła do trwałej kolejki ({job.job_id}). "
            "Możesz bezpiecznie zamknąć albo odświeżyć stronę.",
        )
    return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
