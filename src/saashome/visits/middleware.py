import hashlib
import hmac

from django.conf import settings
from django.db import DatabaseError

from .models import Visit


class VisitTrackingMiddleware:
    ignored_path_prefixes = (
        "/admin/",
        "/visits/",
        "/static/",
    )
    ignored_paths = (
        "/favicon.ico",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        self.track_visit(request, response)
        return response

    def should_track(self, request, response):
        if request.method != "GET":
            return False
        if response.status_code >= 400:
            return False
        if request.path in self.ignored_paths:
            return False
        return not request.path.startswith(self.ignored_path_prefixes)

    def track_visit(self, request, response):
        if not self.should_track(request, response):
            return

        session_key = self.get_session_key(request)

        try:
            Visit.objects.create(
                url_path=request.path,
                full_url=request.build_absolute_uri(),
                page_type=self.get_page_type(request),
                franchise_id=self.get_franchise_id(request),
                user=self.get_user(request),
                session_key=session_key,
                referrer=request.META.get("HTTP_REFERER", ""),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                ip_hash=self.get_ip_hash(request),
            )
        except DatabaseError:
            # The course project may run before migrations are applied.
            pass

    def get_session_key(self, request):
        if not hasattr(request, "session"):
            return ""
        if not request.session.session_key:
            request.session.create()
        return request.session.session_key or ""

    def get_page_type(self, request):
        match = getattr(request, "resolver_match", None)
        if match and match.url_name:
            return match.url_name
        return request.path.strip("/") or "home"

    def get_franchise_id(self, request):
        match = getattr(request, "resolver_match", None)
        candidate = None

        if match:
            candidate = (
                match.kwargs.get("franchise_id")
                or match.kwargs.get("franchise_pk")
                or match.kwargs.get("franchise")
            )

        candidate = candidate or request.GET.get("franchise_id") or request.GET.get("franchise")
        if not candidate:
            return None

        try:
            return int(candidate)
        except (TypeError, ValueError):
            return None

    def get_user(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            return user
        return None

    def get_ip_hash(self, request):
        ip_address = self.get_ip_address(request)
        if not ip_address:
            return ""
        return hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            ip_address.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def get_ip_address(self, request):
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")
