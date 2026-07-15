from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from franchises.models import Franchise
from visits.models import VisitEvent

from .forms import MultiFranchiseLeadForm
from .services import (
    create_multi_franchise_leads,
    create_visit_event_for_request,
    get_saved_franchise_ids_for_user,
    get_saved_franchises_for_user,
    is_franchise_saved_by_user,
    save_franchise_for_user,
    unsave_franchise_for_user,
)


def safe_redirect(request, fallback_url):
    referer = request.META.get("HTTP_REFERER", "")
    if referer and url_has_allowed_host_and_scheme(
        referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(referer)
    return redirect(fallback_url)


def parse_selected_ids(request):
    ids_value = request.GET.get("ids", "")
    selected_ids = []
    if ids_value:
        selected_ids = [item.strip() for item in ids_value.split(",") if item.strip()]
    selected_ids.extend(request.GET.getlist("selected_franchises"))
    clean_ids = []
    for value in selected_ids:
        try:
            clean_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return clean_ids


@login_required
def saved_franchise_list_view(request):
    saved_items = get_saved_franchises_for_user(request.user)
    context = {
        "site_name": "Porównaj Franczyzę",
        "page_title": "Saved franchises",
        "active_page": "saved",
        "saved_items": saved_items,
        "franchises": [item.franchise for item in saved_items],
    }
    return render(request, "shortlists/saved_list.html", context)


@login_required
@require_POST
def save_franchise_view(request, slug):
    franchise = get_object_or_404(Franchise, slug=slug, is_active=True)
    saved, created = save_franchise_for_user(
        request.user,
        franchise,
        session_key=request.session.session_key or "",
    )
    create_visit_event_for_request(
        request,
        VisitEvent.EVENT_SAVE_FRANCHISE,
        value=franchise.name,
        metadata={"franchise_id": franchise.id, "created": created},
    )
    if created:
        messages.success(request, f"Dodano {franchise.name} do zapisanych franczyz.")
    else:
        messages.info(request, f"{franchise.name} jest już na Twojej liście.")
    return safe_redirect(request, franchise.get_absolute_url())


@login_required
@require_POST
def unsave_franchise_view(request, slug):
    franchise = get_object_or_404(Franchise, slug=slug)
    deleted_count = unsave_franchise_for_user(request.user, franchise)
    create_visit_event_for_request(
        request,
        VisitEvent.EVENT_UNSAVE_FRANCHISE,
        value=franchise.name,
        metadata={"franchise_id": franchise.id, "deleted_count": deleted_count},
    )
    if deleted_count:
        messages.success(request, f"Usunięto {franchise.name} z zapisanych franczyz.")
    else:
        messages.info(request, f"{franchise.name} nie było na Twojej liście.")
    return safe_redirect(request, reverse("shortlists:saved_list"))


@login_required
def compare_saved_franchises_view(request):
    saved_ids = get_saved_franchise_ids_for_user(request.user)
    selected_ids = [franchise_id for franchise_id in parse_selected_ids(request) if franchise_id in saved_ids]
    if selected_ids:
        selected_ids = selected_ids[:4]
        franchises = list(
            Franchise.objects.filter(id__in=selected_ids, is_active=True)
            .select_related("category")
        )
        franchises.sort(key=lambda franchise: selected_ids.index(franchise.id))
    else:
        franchises = [item.franchise for item in get_saved_franchises_for_user(request.user)[:4]]

    create_visit_event_for_request(
        request,
        VisitEvent.EVENT_COMPARE_FRANCHISES,
        value=str(len(franchises)),
        metadata={"franchise_ids": [franchise.id for franchise in franchises]},
    )
    context = {
        "site_name": "Porównaj Franczyzę",
        "page_title": "Compare franchises",
        "active_page": "saved",
        "franchises": franchises,
    }
    return render(request, "shortlists/compare.html", context)


@login_required
def multi_request_info_view(request):
    initial = {}
    selected_ids = parse_selected_ids(request)
    if selected_ids:
        allowed_ids = get_saved_franchise_ids_for_user(request.user)
        initial["selected_franchises"] = [franchise_id for franchise_id in selected_ids if franchise_id in allowed_ids][:5]

    if request.method == "POST":
        form = MultiFranchiseLeadForm(request.POST, user=request.user)
        if form.is_valid():
            created_leads, skipped_duplicates = create_multi_franchise_leads(
                request.user,
                list(form.cleaned_data["selected_franchises"]),
                form.cleaned_data,
                request=request,
            )
            create_visit_event_for_request(
                request,
                VisitEvent.EVENT_MULTI_REQUEST_SUBMIT,
                value=str(len(created_leads)),
                metadata={
                    "created_lead_ids": [lead.id for lead in created_leads],
                    "skipped_franchise_ids": [franchise.id for franchise in skipped_duplicates],
                },
            )
            messages.success(
                request,
                f"Utworzono {len(created_leads)} zgłoszeń. Pominięto duplikaty z ostatnich 24h: {len(skipped_duplicates)}.",
            )
            return redirect("shortlists:saved_list")
    else:
        form = MultiFranchiseLeadForm(user=request.user, initial=initial)

    context = {
        "site_name": "Porównaj Franczyzę",
        "page_title": "Request information",
        "active_page": "saved",
        "form": form,
        "saved_count": get_saved_franchises_for_user(request.user).count(),
    }
    return render(request, "shortlists/multi_request.html", context)
