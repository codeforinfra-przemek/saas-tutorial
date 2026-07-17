from django.urls import path

from .views import (
    billing_success_view,
    checkout_view,
    customer_portal_view,
    franchise_subscription_detail_view,
    franchise_subscription_list_view,
    franchise_subscription_request_view,
    investor_services_view,
    pricing_view,
    subscription_request_manage_view,
    subscription_request_review_view,
    stripe_webhook_view,
    vendor_billing_view,
    vendor_pricing_view,
)


app_name = "billing"

urlpatterns = [
    path("pricing/", pricing_view, name="pricing"),
    path("pricing/investor/", investor_services_view, name="investor_services"),
    path("pricing/vendor/", vendor_pricing_view, name="vendor_pricing"),
    path("billing/checkout/<slug:plan_slug>/", checkout_view, name="checkout"),
    path("billing/success/", billing_success_view, name="success"),
    path("billing/customer-portal/", customer_portal_view, name="customer_portal"),
    path("billing/webhooks/stripe/", stripe_webhook_view, name="stripe_webhook"),
    path("vendor/billing/", vendor_billing_view, name="vendor_billing"),
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
