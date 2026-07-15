from django.conf import settings
from django.contrib import messages
from django.db import OperationalError, ProgrammingError
from django.shortcuts import redirect, render

from accounts.permissions import has_active_vendor_membership, vendor_required

from .forms import InvestorServiceRequestForm
from .models import InvestorServiceRequest, Plan


SPECIALIST_AREAS = (
    ("location", "Lokal i analiza lokalizacji"),
    ("legal", "Prawo i umowa franczyzowa"),
    ("design_build", "Projekt, adaptacja i budowa lokalu"),
    ("finance", "Finansowanie inwestycji"),
    ("operations", "Operacje i otwarcie placówki"),
)


def investor_services_view(request):
    initial = {"service_type": request.GET.get("service", InvestorServiceRequest.SERVICE_LOCATION_REPORT)}
    specialist_area = request.GET.get("specialist", "")
    if specialist_area:
        initial.update(
            {
                "service_type": InvestorServiceRequest.SERVICE_SPECIALIST_MATCH,
                "specialist_area": specialist_area,
            }
        )
    if request.user.is_authenticated:
        initial.update(
            {
                "name": request.user.get_full_name() or request.user.username,
                "email": request.user.email,
            }
        )

    form = InvestorServiceRequestForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        service_request = form.save(commit=False)
        if request.user.is_authenticated:
            service_request.user = request.user
        service_request.save()
        messages.success(
            request,
            "Zapisaliśmy zgłoszenie. Skontaktujemy się z Tobą, aby potwierdzić zakres i sposób płatności.",
        )
        return redirect("billing:investor_services")

    return render(
        request,
        "billing/investor_services.html",
        {
            "site_name": "Porównaj Franczyzę",
            "page_title": "Usługi dla inwestora",
            "active_page": "investor_services",
            "form": form,
            "specialist_areas": SPECIALIST_AREAS,
        },
    )


def pricing_view(request):
    if not has_active_vendor_membership(request.user):
        return investor_services_view(request)
    return vendor_pricing_view(request)


@vendor_required
def vendor_pricing_view(request):
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
