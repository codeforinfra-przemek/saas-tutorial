from django.urls import path

from .views import vendor_dashboard_view


app_name = "vendor"

urlpatterns = [
    path("", vendor_dashboard_view, name="dashboard"),
]
