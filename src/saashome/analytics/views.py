from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .services import get_admin_analytics, get_vendor_analytics


@login_required
def vendor_analytics_view(request):
    range_key = request.GET.get("range", "30d")
    analytics = get_vendor_analytics(request.user, range_key=range_key)
    context = {
        "site_name": "SaaS Home",
        "page_title": "Analytics",
        "active_page": "vendor",
        "analytics": analytics,
        "range_options": ("7d", "30d", "90d"),
    }
    return render(request, "analytics/vendor_dashboard.html", context)


@staff_member_required
def admin_analytics_view(request):
    range_key = request.GET.get("range", "30d")
    analytics = get_admin_analytics(range_key=range_key)
    context = {
        "site_name": "SaaS Home",
        "page_title": "Internal analytics",
        "active_page": "analytics",
        "analytics": analytics,
        "range_options": ("7d", "30d", "90d"),
    }
    return render(request, "analytics/admin_dashboard.html", context)
