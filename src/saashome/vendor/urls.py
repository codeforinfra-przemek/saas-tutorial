from django.urls import path

from .views import (
    vendor_dashboard_view,
    vendor_franchise_edit_view,
    vendor_franchise_asset_delete_view,
    vendor_franchise_asset_review_view,
    vendor_franchise_list_view,
    vendor_franchise_media_view,
    vendor_franchise_update_submit_view,
    vendor_lead_detail_view,
    vendor_lead_list_view,
)


app_name = "vendor"

urlpatterns = [
    path("", vendor_dashboard_view, name="dashboard"),
    path("franchises/", vendor_franchise_list_view, name="franchises"),
    path("franchises/<slug:slug>/edit/", vendor_franchise_edit_view, name="franchise_edit"),
    path("franchises/<slug:slug>/media/", vendor_franchise_media_view, name="franchise_media"),
    path(
        "franchises/<slug:slug>/media/<int:pk>/delete/",
        vendor_franchise_asset_delete_view,
        name="franchise_asset_delete",
    ),
    path(
        "franchises/<slug:slug>/media/<int:pk>/review/<str:decision>/",
        vendor_franchise_asset_review_view,
        name="franchise_asset_review",
    ),
    path("franchise-updates/<int:pk>/submit/", vendor_franchise_update_submit_view, name="franchise_update_submit"),
    path("leads/", vendor_lead_list_view, name="leads"),
    path("leads/<int:pk>/", vendor_lead_detail_view, name="lead_detail"),
]
