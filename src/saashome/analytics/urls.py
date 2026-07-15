from django.urls import path

from .views import admin_analytics_view, vendor_analytics_view


app_name = "analytics"

urlpatterns = [
    path("vendor/analytics/", vendor_analytics_view, name="vendor_analytics"),
    path("internal/analytics/", admin_analytics_view, name="admin_analytics"),
]
