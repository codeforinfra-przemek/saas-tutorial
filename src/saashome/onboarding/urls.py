from django.urls import path

from .views import claim_profile_view, vendor_claims_list_view


app_name = "onboarding"

urlpatterns = [
    path("franchises/<slug:slug>/claim/", claim_profile_view, name="claim_profile"),
    path("vendor/claims/", vendor_claims_list_view, name="vendor_claims"),
]
