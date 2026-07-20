from django.urls import path

from .views import (
    franchise_detail_view,
    franchise_compare_view,
    franchise_directory_view,
    franchise_list_view,
    franchise_location_create_view,
    franchise_location_delete_view,
    franchise_location_edit_view,
    franchise_manage_create_view,
    franchise_manage_delete_view,
    franchise_manage_detail_view,
    franchise_manage_edit_view,
    franchise_manage_list_view,
    franchise_research_detail_view,
)
from leads.views import create_lead_view


app_name = "franchises"

urlpatterns = [
    path("", franchise_list_view, name="list"),
    path("directory/", franchise_directory_view, name="directory"),
    path("compare/", franchise_compare_view, name="compare"),
    path("manage/", franchise_manage_list_view, name="manage_list"),
    path("manage/new/", franchise_manage_create_view, name="manage_create"),
    path("manage/<int:pk>/", franchise_manage_detail_view, name="manage_detail"),
    path("manage/<int:pk>/edit/", franchise_manage_edit_view, name="manage_edit"),
    path("manage/<int:pk>/delete/", franchise_manage_delete_view, name="manage_delete"),
    path("manage/<int:franchise_pk>/locations/new/", franchise_location_create_view, name="location_create"),
    path("manage/locations/<int:pk>/edit/", franchise_location_edit_view, name="location_edit"),
    path("manage/locations/<int:pk>/delete/", franchise_location_delete_view, name="location_delete"),
    path("<slug:slug>/request-info/", create_lead_view, name="lead_create"),
    path("<slug:slug>/profile/", franchise_detail_view, {"data_only": True}, name="profile"),
    path("<slug:slug>/research/", franchise_research_detail_view, name="research_detail"),
    path("<slug:slug>/", franchise_detail_view, name="detail"),
]
