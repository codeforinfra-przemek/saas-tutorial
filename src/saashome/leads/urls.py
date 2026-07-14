from django.urls import path

from .views import (
    lead_create_view,
    lead_delete_view,
    lead_detail_view,
    lead_edit_view,
    lead_list_view,
)


app_name = "leads"

urlpatterns = [
    path("", lead_list_view, name="list"),
    path("new/", lead_create_view, name="create"),
    path("<int:pk>/", lead_detail_view, name="detail"),
    path("<int:pk>/edit/", lead_edit_view, name="edit"),
    path("<int:pk>/delete/", lead_delete_view, name="delete"),
]
