from django.urls import path

from .views import investor_services_view, pricing_view, vendor_pricing_view


app_name = "billing"

urlpatterns = [
    path("pricing/", pricing_view, name="pricing"),
    path("pricing/investor/", investor_services_view, name="investor_services"),
    path("pricing/vendor/", vendor_pricing_view, name="vendor_pricing"),
]
