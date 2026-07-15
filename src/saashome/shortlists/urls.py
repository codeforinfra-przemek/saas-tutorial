from django.urls import path

from .views import (
    compare_saved_franchises_view,
    multi_request_info_view,
    save_franchise_view,
    saved_franchise_list_view,
    unsave_franchise_view,
)


app_name = "shortlists"

urlpatterns = [
    path("saved/", saved_franchise_list_view, name="saved_list"),
    path("saved/compare/", compare_saved_franchises_view, name="compare"),
    path("saved/request-info/", multi_request_info_view, name="multi_request"),
    path("franchises/<slug:slug>/save/", save_franchise_view, name="save"),
    path("franchises/<slug:slug>/unsave/", unsave_franchise_view, name="unsave"),
]
