import logging
from smtplib import SMTPException

from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.conf import settings
from django.core.mail import BadHeaderError, EmailMultiAlternatives
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import FormView

from .forms import ProfileForm, SignupForm, UserProfileForm
from .models import UserProfile


logger = logging.getLogger(__name__)


def send_signup_welcome_email(user):
    if not user.email:
        return

    context = {
        "user": user,
        "site_name": "SaaS Home",
        "contact_email": settings.DEFAULT_FROM_EMAIL,
    }
    subject = "Witaj w SaaS Home"
    text_body = render_to_string("emails/signup_welcome.txt", context)
    html_body = render_to_string("emails/signup_welcome.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
        reply_to=[settings.DEFAULT_FROM_EMAIL],
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)


class SignupView(FormView):
    template_name = "accounts/signup.html"
    form_class = SignupForm
    success_url = reverse_lazy("accounts:dashboard")

    def form_valid(self, form):
        user = form.save()
        login(self.request, user, backend="accounts.backends.EmailBackend")
        try:
            send_signup_welcome_email(user)
        except (BadHeaderError, OSError, SMTPException):
            logger.exception("Could not send signup welcome email.")
            messages.warning(
                self.request,
                "Konto utworzone. Nie udalo sie wyslac maila powitalnego.",
            )
        else:
            messages.success(
                self.request,
                "Konto utworzone. Wyslalismy mail powitalny.",
            )
        return super().form_valid(form)


@login_required
def dashboard_view(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    organizations = request.user.organization_memberships.select_related("organization")
    context = {
        "site_name": "SaaS Home",
        "page_title": "Dashboard",
        "profile": profile,
        "memberships": organizations,
    }
    return render(request, "accounts/dashboard.html", context)


@login_required
def profile_view(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
    user_form = UserProfileForm(instance=request.user)
    profile_form = ProfileForm(instance=profile)
    password_form = PasswordChangeForm(user=request.user)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "profile":
            user_form = UserProfileForm(request.POST, instance=request.user)
            profile_form = ProfileForm(request.POST, request.FILES, instance=profile)
            if user_form.is_valid() and profile_form.is_valid():
                user_form.save()
                profile = profile_form.save(commit=False)
                if profile_form.cleaned_data.get("remove_avatar") and profile.avatar:
                    profile.avatar.delete(save=False)
                    profile.avatar = ""
                profile.save()
                messages.success(request, "Profil został zaktualizowany.")
                return redirect("accounts:profile")
            messages.error(request, "Sprawdź dane profilu i spróbuj ponownie.")

        if action == "password":
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Hasło zostało zmienione.")
                return redirect("accounts:profile")
            messages.error(request, "Nie udało się zmienić hasła. Sprawdź formularz.")

    context = {
        "site_name": "SaaS Home",
        "page_title": "Profil użytkownika",
        "user_form": user_form,
        "profile_form": profile_form,
        "password_form": password_form,
        "profile": profile,
    }
    return render(request, "accounts/profile.html", context)
