from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """Keep GitHub social-login behavior explicit and provider-scoped."""

    def is_email_verified(self, provider, email):
        if provider.id == "github":
            return True
        return super().is_email_verified(provider, email)
