from django.urls import path

from .views import (
    franchise_subscription_detail_view,
    franchise_subscription_list_view,
    franchise_subscription_request_view,
    investor_services_view,
    pricing_view,
    subscription_request_manage_view,
    subscription_request_review_view,
    vendor_pricing_view,
)


app_name = "billing"

urlpatterns = [
    path("pricing/", pricing_view, name="pricing"),
    path("pricing/investor/", investor_services_view, name="investor_services"),
    path("pricing/vendor/", vendor_pricing_view, name="vendor_pricing"),
    path("subscriptions/", franchise_subscription_list_view, name="subscriptions"),
    path("subscriptions/<slug:slug>/", franchise_subscription_detail_view, name="subscription_detail"),
    path(
        "subscriptions/<slug:slug>/request/<str:action>/",
        franchise_subscription_request_view,
        name="subscription_request",
    ),
    path("manage/subscription-requests/", subscription_request_manage_view, name="manage_requests"),
    path(
        "manage/subscription-requests/<int:pk>/<str:decision>/",
        subscription_request_review_view,
        name="review_request",
    ),
]
