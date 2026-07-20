from django.contrib import messages
import hashlib

from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from billing.models import OrganizationSubscription, Plan
from franchises.forms import (
    ResearchDocumentUploadForm,
    ResearchReviewFieldForm,
    ResearchWorkspaceDecisionForm,
)
from franchises.models import (
    FranchiseResearchDocument,
    FranchiseResearchEvent,
    FranchiseResearchReviewField,
    FranchiseResearchWorkspace,
)

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
    return render(
        request,
        "backoffice/research_workbench_list.html",
        internal_context(
            page_title="Human Research Workbench",
            workspaces=workspaces,
            filters={"q": q, "status": status},
            status_choices=FranchiseResearchWorkspace.STATUS_CHOICES,
        ),
    )


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
    fields = workspace.review_fields.select_related("decided_by")
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
        if field.task_id != current_task_id:
            current_task_id = field.task_id
            current_group = {
                "task_id": field.task_id,
                "title": field.task_title,
                "fields": [],
            }
            grouped_fields.append(current_group)
        current_group["fields"].append(field)
    stages = []
    for stage in workspace.stage_summary:
        rendered = dict(stage)
        if rendered["key"] == "review":
            rendered["status"] = (
                "complete"
                if workspace.status
                in {
                    FranchiseResearchWorkspace.STATUS_READY,
                    FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS,
                }
                else "attention"
                if workspace.status == FranchiseResearchWorkspace.STATUS_REJECTED
                else "current"
            )
            rendered["summary"] = f"{_workspace_counts(workspace)['progress']}% sprawdzone"
        if rendered["key"] == "import" and workspace.status == FranchiseResearchWorkspace.STATUS_APPROVED_WITH_GAPS:
            rendered["status"] = "current"
            rendered["summary"] = "gotowe do eksportu/importu"
        stages.append(rendered)
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
            documents=workspace.documents.select_related("uploaded_by"),
            events=workspace.events.select_related("actor")[:20],
        ),
    )


def _reopen_workspace_if_needed(workspace):
    if workspace.status != FranchiseResearchWorkspace.STATUS_REVIEW:
        workspace.status = FranchiseResearchWorkspace.STATUS_REVIEW
        workspace.reviewed_by = None
        workspace.reviewed_at = None
        workspace.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"]
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


@staff_member_required
@require_POST
def research_workbench_field_action_view(request, workspace_id, pk, action):
    workspace = get_object_or_404(FranchiseResearchWorkspace, workspace_id=workspace_id)
    field = get_object_or_404(workspace.review_fields, pk=pk)
    decisions = {
        "accept": FranchiseResearchReviewField.DECISION_ACCEPTED,
        "reject": FranchiseResearchReviewField.DECISION_REJECTED,
        "gap": FranchiseResearchReviewField.DECISION_DOCUMENTED_GAP,
        "reset": FranchiseResearchReviewField.DECISION_PENDING,
    }
    if action not in decisions:
        raise Http404
    if action == "accept" and not field.effective_value:
        messages.error(request, "Najpierw wpisz wartość albo oznacz pole jako udokumentowany brak.")
        return _workbench_return(request, workspace_id, anchor=f"field-{field.pk}")
    field.decision = decisions[action]
    if action == "reset":
        field.decided_by = None
        field.decided_at = None
    else:
        field.decided_by = request.user
        field.decided_at = timezone.now()
    field.save(update_fields=["decision", "decided_by", "decided_at", "updated_at"])
    _reopen_workspace_if_needed(workspace)
    FranchiseResearchEvent.objects.create(
        workspace=workspace,
        event_type="field_decision",
        message=f"{field.target_field}: {field.get_decision_display()}.",
        metadata={"field_id": field.pk, "decision": field.decision},
        actor=request.user,
    )
    messages.success(request, f"Zapisano decyzję dla pola „{field.target_field}”.")
    return _workbench_return(request, workspace_id, anchor=f"field-{field.pk}")


@staff_member_required
@require_POST
def research_workbench_field_edit_view(request, workspace_id, pk):
    workspace = get_object_or_404(FranchiseResearchWorkspace, workspace_id=workspace_id)
    field = get_object_or_404(workspace.review_fields, pk=pk)
    form = ResearchReviewFieldForm(request.POST, instance=field)
    if not form.is_valid():
        messages.error(request, "Nie udało się zapisać korekty. Sprawdź wpisane dane.")
    elif not form.cleaned_data["reviewer_value"].strip():
        messages.error(request, "Wartość po korekcie nie może być pusta.")
    else:
        field = form.save(commit=False)
        field.decision = FranchiseResearchReviewField.DECISION_ACCEPTED_EDITED
        field.decided_by = request.user
        field.decided_at = timezone.now()
        field.save()
        _reopen_workspace_if_needed(workspace)
        FranchiseResearchEvent.objects.create(
            workspace=workspace,
            event_type="field_edited",
            message=f"Uzupełniono i zatwierdzono pole {field.target_field}.",
            metadata={"field_id": field.pk},
            actor=request.user,
        )
        messages.success(request, "Korekta została zapisana i zaakceptowana.")
    return _workbench_return(request, workspace_id, anchor=f"field-{field.pk}")


@staff_member_required
@require_POST
def research_workbench_document_upload_view(request, workspace_id):
    workspace = get_object_or_404(FranchiseResearchWorkspace, workspace_id=workspace_id)
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
    form = ResearchWorkspaceDecisionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Nie udało się zapisać decyzji końcowej.")
        return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
    counts = _workspace_counts(workspace)
    if action == "ready":
        if counts["pending"]:
            messages.error(
                request,
                f"Pozostało {counts['pending']} pól bez decyzji. Zatwierdź, odrzuć lub oznacz je jako brak.",
            )
            return redirect("backoffice:research_workbench_detail", workspace_id=workspace_id)
        status = FranchiseResearchWorkspace.STATUS_READY
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
