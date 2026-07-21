"""Keep credential-bearing connection URLs out of Django debug reports."""

from django.views.debug import SafeExceptionReporterFilter


class CredentialSafeExceptionReporterFilter(SafeExceptionReporterFilter):
    """Extend Django's masking to URL-style credentials in request META."""

    sensitive_meta_names = {
        "DATABASE_URL",
        "REDIS_URL",
        "CELERY_BROKER_URL",
    }

    def get_safe_request_meta(self, request):
        cleaned = super().get_safe_request_meta(request)
        for name in self.sensitive_meta_names:
            if name in cleaned:
                cleaned[name] = self.cleansed_substitute
        return cleaned
