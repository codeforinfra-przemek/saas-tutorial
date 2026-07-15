from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from franchises.models import Franchise

from .forms import ClaimProfileRequestForm
from .models import ClaimProfileRequest
from .services import notify_new_claim_request


@login_required
def claim_profile_view(request, slug):
    franchise = get_object_or_404(Franchise, slug=slug, is_active=True)
    if franchise.organization_id:
        messages.info(request, "Ten profil jest już zarządzany przez organizację.")
        return redirect("franchises:detail", slug=franchise.slug)

    existing_claim = ClaimProfileRequest.objects.filter(
        franchise=franchise,
        user=request.user,
        status__in=(ClaimProfileRequest.STATUS_NEW, ClaimProfileRequest.STATUS_IN_REVIEW),
    ).first()
    if existing_claim:
        return render(request, "onboarding/claim_profile.html", {
            "site_name": "SaaS Home", "page_title": "Claim profile", "active_page": "vendor",
            "franchise": franchise, "existing_claim": existing_claim,
        })

    initial = {
        "claimant_email": request.user.email,
        "claimant_name": request.user.get_full_name() or request.user.username,
        "company_name": franchise.name,
    }
    form = ClaimProfileRequestForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        claim = form.save(commit=False)
        claim.franchise = franchise
        claim.user = request.user
        claim.status = ClaimProfileRequest.STATUS_NEW
        claim.save()
        notify_new_claim_request(claim, request=request)
        messages.success(request, "Zgłoszenie zostało wysłane do weryfikacji.")
        return redirect("onboarding:vendor_claims")

    return render(request, "onboarding/claim_profile.html", {
        "site_name": "SaaS Home", "page_title": "Claim profile", "active_page": "vendor",
        "franchise": franchise, "form": form,
    })


@login_required
def vendor_claims_list_view(request):
    claims = ClaimProfileRequest.objects.filter(user=request.user).select_related("franchise", "organization")
    return render(request, "onboarding/vendor_claims_list.html", {
        "site_name": "SaaS Home", "page_title": "My claim requests", "active_page": "vendor", "claims": claims,
    })
