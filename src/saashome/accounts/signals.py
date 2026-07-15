from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from allauth.account.signals import email_confirmed
from allauth.socialaccount.signals import social_account_added, social_account_updated

from .models import UserProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.get_or_create(user=instance)


@receiver(email_confirmed)
def mark_profile_email_verified(request, email_address, **kwargs):
    profile, _ = UserProfile.objects.get_or_create(user=email_address.user)
    if not profile.email_verified:
        profile.email_verified = True
        profile.save(update_fields=["email_verified", "updated_at"])


def mark_github_user_verified(sociallogin):
    if sociallogin.account.provider != "github":
        return
    profile, _ = UserProfile.objects.get_or_create(user=sociallogin.user)
    if not profile.email_verified:
        profile.email_verified = True
        profile.save(update_fields=["email_verified", "updated_at"])


@receiver(social_account_added)
def mark_new_github_user_verified(request, sociallogin, **kwargs):
    mark_github_user_verified(sociallogin)


@receiver(social_account_updated)
def mark_returning_github_user_verified(request, sociallogin, **kwargs):
    mark_github_user_verified(sociallogin)
