import hashlib
import hmac

from django.conf import settings

from .models import Visit, VisitEvent


def ensure_session_key(request):
    if not hasattr(request, "session"):
        return ""
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key or ""


def get_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def hash_ip(ip_address):
    if not ip_address:
        return ""
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        ip_address.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_visit(request, page_type=Visit.PAGE_TYPE_OTHER, franchise=None):
    session_key = ensure_session_key(request)
    visit = Visit.objects.create(
        user=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
        session_key=session_key,
        path=request.path,
        full_path=request.get_full_path(),
        page_type=page_type,
        franchise=franchise,
        referrer=request.META.get("HTTP_REFERER", ""),
        utm_source=request.GET.get("utm_source", ""),
        utm_medium=request.GET.get("utm_medium", ""),
        utm_campaign=request.GET.get("utm_campaign", ""),
        utm_content=request.GET.get("utm_content", ""),
        utm_term=request.GET.get("utm_term", ""),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        ip_hash=hash_ip(get_client_ip(request)),
    )
    VisitEvent.objects.create(visit=visit, event_type=VisitEvent.EVENT_PAGE_VIEW)
    request.session["last_visit_id"] = visit.id
    if franchise is not None:
        request.session["last_franchise_visit_id"] = visit.id
        request.session["last_franchise_id"] = franchise.id
    return visit
