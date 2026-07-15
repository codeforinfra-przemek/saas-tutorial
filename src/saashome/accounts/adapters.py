from allauth.account.adapter import DefaultAccountAdapter

from .models import UserProfile


class AccountAdapter(DefaultAccountAdapter):
    def save_user(self, request, user, form, commit=True):
        user = super().save_user(request, user, form, commit=False)
        user.is_active = False
        if commit:
            user.save()
            UserProfile.objects.get_or_create(user=user)
        return user

    def confirm_email(self, request, email_address):
        confirmed = super().confirm_email(request, email_address)
        user = email_address.user
        if confirmed and not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])
        return confirmed
