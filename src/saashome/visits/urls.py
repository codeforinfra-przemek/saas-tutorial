from django.urls import path

from .views import visit_list_view


app_name = "visits"

urlpatterns = [
    path("", visit_list_view, name="list"),
]
