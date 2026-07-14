from django.conf import settings
from django.shortcuts import render

from .models import Plan


def pricing_view(request):
    plans = Plan.objects.filter(is_active=True).order_by("sort_order", "price_monthly", "name")
    context = {
        "site_name": "Porównaj Franczyzę",
        "page_title": "Pricing",
        "active_page": "pricing",
        "plans": plans,
        "contact_email": settings.DEFAULT_FROM_EMAIL,
    }
    return render(request, "billing/pricing.html", context)
