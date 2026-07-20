from django.urls import path

from .views import internal_home_view, internal_revenue_dashboard_view, internal_sales_dashboard_view, internal_sales_opportunity_detail_view, internal_subscriptions_view


app_name = "backoffice"

urlpatterns = [
    path("internal/", internal_home_view, name="internal_home"),
    path("internal/revenue/", internal_revenue_dashboard_view, name="revenue_dashboard"),
    path("internal/revenue/subscriptions/", internal_subscriptions_view, name="subscriptions"),
    path("internal/sales/", internal_sales_dashboard_view, name="sales_dashboard"),
    path("internal/sales/opportunities/<int:pk>/", internal_sales_opportunity_detail_view, name="sales_opportunity_detail"),
]
