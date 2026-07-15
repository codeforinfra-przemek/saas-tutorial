from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from allauth.account.signals import email_confirmed

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
