from django.urls import path

from .views import franchise_detail_view, franchise_list_view
from leads.views import create_lead_view


app_name = "franchises"

urlpatterns = [
    path("", franchise_list_view, name="list"),
    path("<slug:slug>/request-info/", create_lead_view, name="lead_create"),
    path("<slug:slug>/", franchise_detail_view, name="detail"),
]
