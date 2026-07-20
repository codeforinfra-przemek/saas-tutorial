from django.urls import path

from .views import (
    internal_home_view,
    internal_revenue_dashboard_view,
    internal_sales_dashboard_view,
    internal_sales_opportunity_detail_view,
    internal_subscriptions_view,
    research_workbench_decision_view,
    research_workbench_detail_view,
    research_workbench_document_download_view,
    research_workbench_document_upload_view,
    research_workbench_field_action_view,
    research_workbench_field_edit_view,
    research_workbench_job_cancel_view,
    research_workbench_job_queue_view,
    research_workbench_job_status_view,
    research_workbench_list_view,
)


app_name = "backoffice"

urlpatterns = [
    path("internal/", internal_home_view, name="internal_home"),
    path("internal/revenue/", internal_revenue_dashboard_view, name="revenue_dashboard"),
    path("internal/revenue/subscriptions/", internal_subscriptions_view, name="subscriptions"),
    path("internal/sales/", internal_sales_dashboard_view, name="sales_dashboard"),
    path("internal/sales/opportunities/<int:pk>/", internal_sales_opportunity_detail_view, name="sales_opportunity_detail"),
    path("internal/research/", research_workbench_list_view, name="research_workbench_list"),
    path("internal/research/<uuid:workspace_id>/", research_workbench_detail_view, name="research_workbench_detail"),
    path("internal/research/<uuid:workspace_id>/fields/<int:pk>/edit/", research_workbench_field_edit_view, name="research_workbench_field_edit"),
    path("internal/research/<uuid:workspace_id>/fields/<int:pk>/<str:action>/", research_workbench_field_action_view, name="research_workbench_field_action"),
    path("internal/research/<uuid:workspace_id>/documents/upload/", research_workbench_document_upload_view, name="research_workbench_document_upload"),
    path("internal/research/<uuid:workspace_id>/documents/<int:pk>/download/", research_workbench_document_download_view, name="research_workbench_document_download"),
    path("internal/research/<uuid:workspace_id>/jobs/queue/", research_workbench_job_queue_view, name="research_workbench_job_queue"),
    path("internal/research/<uuid:workspace_id>/jobs/<uuid:job_id>/cancel/", research_workbench_job_cancel_view, name="research_workbench_job_cancel"),
    path("internal/research/<uuid:workspace_id>/jobs/<uuid:job_id>/status/", research_workbench_job_status_view, name="research_workbench_job_status"),
    path("internal/research/<uuid:workspace_id>/decision/<str:action>/", research_workbench_decision_view, name="research_workbench_decision"),
]
