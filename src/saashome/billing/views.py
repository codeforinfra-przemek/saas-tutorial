from django.conf import settings
from django.db import OperationalError, ProgrammingError
from django.shortcuts import render

from .models import Plan


def pricing_view(request):
    try:
        plans = list(Plan.objects.filter(is_active=True).order_by("sort_order", "price_monthly", "name"))
    except (OperationalError, ProgrammingError):
        plans = []
    context = {
        "site_name": "Porównaj Franczyzę",
        "page_title": "Pricing",
        "active_page": "pricing",
        "plans": plans,
        "contact_email": settings.DEFAULT_FROM_EMAIL,
    }
    return render(request, "billing/pricing.html", context)
