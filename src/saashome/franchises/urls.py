from django.urls import path

from .views import franchise_detail_view, franchise_list_view


app_name = "franchises"

urlpatterns = [
    path("", franchise_list_view, name="list"),
    path("<slug:slug>/", franchise_detail_view, name="detail"),
]
