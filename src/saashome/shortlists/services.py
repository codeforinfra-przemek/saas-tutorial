import uuid
from datetime import timedelta

from django.utils import timezone

from leads.models import Lead, LeadActivity
from leads.services import create_lead_activity, notify_new_lead
from visits.models import Visit, VisitEvent
from visits.services import ensure_session_key, get_client_ip, hash_ip

from .models import SavedFranchise


def save_franchise_for_user(user, franchise, session_key=""):
    if not user or not user.is_authenticated:
        raise ValueError("Saving a franchise requires an authenticated user.")
    return SavedFranchise.objects.get_or_create(
        user=user,
        franchise=franchise,
        defaults={"session_key": session_key or ""},
    )


def unsave_franchise_for_user(user, franchise):
    if not user or not user.is_authenticated:
        return 0
    deleted_count, _ = SavedFranchise.objects.filter(user=user, franchise=franchise).delete()
    return deleted_count


def is_franchise_saved_by_user(user, franchise):
    if not user or not user.is_authenticated:
        return False
    return SavedFranchise.objects.filter(user=user, franchise=franchise).exists()


def get_saved_franchises_for_user(user):
    if not user or not user.is_authenticated:
        return SavedFranchise.objects.none()
    return (
        SavedFranchise.objects.filter(user=user, franchise__is_active=True)
        .select_related("franchise", "franchise__category", "franchise__organization")
        .order_by("-created_at")
    )


def get_saved_franchise_ids_for_user(user):
    if not user or not user.is_authenticated:
        return set()
    return set(
        SavedFranchise.objects.filter(user=user, franchise__is_active=True).values_list(
            "franchise_id",
            flat=True,
        )
    )


def recent_lead_exists(email, franchise):
    if not email:
        return False
    since = timezone.now() - timedelta(hours=24)
    return Lead.objects.filter(
        email__iexact=email.strip(),
        franchise=franchise,
        created_at__gte=since,
    ).exists()


def get_related_visit_for_multi_request(request, franchise, session_key):
    visit_id = request.session.get("last_franchise_visit_id") if request else None
    if visit_id:
        visit = Visit.objects.filter(
            id=visit_id,
            franchise=franchise,
            session_key=session_key,
        ).first()
        if visit:
            return visit
    return (
        Visit.objects.filter(
            session_key=session_key,
            franchise=franchise,
            page_type=Visit.PAGE_TYPE_FRANCHISE_DETAIL,
        )
        .order_by("-created_at")
        .first()
    )


def create_multi_franchise_leads(user, franchises, cleaned_data, request=None):
    if not user or not user.is_authenticated:
        raise ValueError("Multi-franchise requests require an authenticated user.")

    allowed_ids = get_saved_franchise_ids_for_user(user)
    selected_franchises = [franchise for franchise in franchises if franchise.id in allowed_ids]
    session_key = ensure_session_key(request) if request else ""
    source_path = request.get_full_path() if request else ""
    referrer = request.META.get("HTTP_REFERER", "") if request else ""
    user_agent = request.META.get("HTTP_USER_AGENT", "") if request else ""
    ip_hash = hash_ip(get_client_ip(request)) if request else ""
    multi_request_id = uuid.uuid4()
    created_leads = []
    skipped_duplicates = []

    for franchise in selected_franchises:
        if recent_lead_exists(cleaned_data.get("email", ""), franchise):
            skipped_duplicates.append(franchise)
            continue

        lead = Lead.objects.create(
            franchise=franchise,
            visit=get_related_visit_for_multi_request(request, franchise, session_key) if request else None,
            user=user,
            name=cleaned_data.get("name", ""),
            email=cleaned_data.get("email", ""),
            phone=cleaned_data.get("phone", "") or "",
            city=cleaned_data.get("city", "") or "",
            investment_budget=cleaned_data.get("investment_budget"),
            message=cleaned_data.get("message", "") or "",
            source_path=source_path,
            referrer=referrer,
            session_key=session_key,
            utm_source=request.GET.get("utm_source", "") if request else "",
            utm_medium=request.GET.get("utm_medium", "") if request else "",
            utm_campaign=request.GET.get("utm_campaign", "") if request else "",
            utm_content=request.GET.get("utm_content", "") if request else "",
            utm_term=request.GET.get("utm_term", "") if request else "",
            user_agent=user_agent,
            ip_hash=ip_hash,
            privacy_consent=cleaned_data.get("privacy_consent", False),
            marketing_consent=cleaned_data.get("marketing_consent", False),
            multi_request_id=multi_request_id,
        )
        create_lead_activity(
            lead,
            LeadActivity.TYPE_LEAD_CREATED,
            user=user,
            metadata={
                "source": "multi_request",
                "multi_request_id": str(multi_request_id),
                "source_path": source_path,
                "utm_source": lead.utm_source,
                "utm_campaign": lead.utm_campaign,
            },
        )
        if lead.visit_id:
            VisitEvent.objects.create(
                visit=lead.visit,
                event_type=VisitEvent.EVENT_SUBMIT_LEAD_FORM,
                value=lead.email,
                metadata={
                    "lead_id": lead.id,
                    "source": "multi_request",
                    "multi_request_id": str(multi_request_id),
                },
            )
        notify_new_lead(lead, request=request)
        created_leads.append(lead)

    return created_leads, skipped_duplicates


def create_visit_event_for_request(request, event_type, value="", metadata=None):
    if not request:
        return None
    session_key = ensure_session_key(request)
    visit_id = request.session.get("last_visit_id")
    visit = None
    if visit_id:
        visit = Visit.objects.filter(id=visit_id, session_key=session_key).first()
    if visit is None:
        visit = Visit.objects.create(
            user=request.user if request.user.is_authenticated else None,
            session_key=session_key,
            path=request.path,
            full_path=request.get_full_path(),
            page_type=Visit.PAGE_TYPE_OTHER,
            referrer=request.META.get("HTTP_REFERER", ""),
            utm_source=request.GET.get("utm_source", ""),
            utm_medium=request.GET.get("utm_medium", ""),
            utm_campaign=request.GET.get("utm_campaign", ""),
            utm_content=request.GET.get("utm_content", ""),
            utm_term=request.GET.get("utm_term", ""),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            ip_hash=hash_ip(get_client_ip(request)),
        )
        request.session["last_visit_id"] = visit.id
    return VisitEvent.objects.create(
        visit=visit,
        event_type=event_type,
        value=value or "",
        metadata=metadata or {},
    )
