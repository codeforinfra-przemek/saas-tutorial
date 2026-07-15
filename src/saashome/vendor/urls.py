from django.urls import path

from .views import (
    vendor_dashboard_view,
    vendor_franchise_edit_view,
    vendor_franchise_list_view,
    vendor_franchise_update_submit_view,
    vendor_lead_detail_view,
    vendor_lead_list_view,
)


app_name = "vendor"

urlpatterns = [
    path("", vendor_dashboard_view, name="dashboard"),
    path("franchises/", vendor_franchise_list_view, name="franchises"),
    path("franchises/<slug:slug>/edit/", vendor_franchise_edit_view, name="franchise_edit"),
    path("franchise-updates/<int:pk>/submit/", vendor_franchise_update_submit_view, name="franchise_update_submit"),
    path("leads/", vendor_lead_list_view, name="leads"),
    path("leads/<int:pk>/", vendor_lead_detail_view, name="lead_detail"),
]
