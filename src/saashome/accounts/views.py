import logging
from smtplib import SMTPException

from django.contrib import messages
from django.contrib.auth import get_user_model, login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.tokens import default_token_generator
from django.conf import settings
from django.core.mail import BadHeaderError, EmailMultiAlternatives
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.generic import FormView, TemplateView

from .forms import ProfileForm, SignupForm, UserProfileForm
from .models import UserProfile


logger = logging.getLogger(__name__)


def send_signup_activation_email(request, user):
    if not user.email:
        return

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    activation_url = request.build_absolute_uri(
        reverse("accounts:activate", kwargs={"uidb64": uid, "token": token})
    )
    context = {
        "user": user,
        "site_name": "SaaS Home",
        "contact_email": settings.DEFAULT_FROM_EMAIL,
        "activation_url": activation_url,
    }
    subject = "Potwierdź rejestrację w SaaS Home"
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
    success_url = reverse_lazy("accounts:activation_sent")

    def form_valid(self, form):
        user = form.save()
        self.request.session["signup_email"] = user.email
        try:
            send_signup_activation_email(self.request, user)
        except (BadHeaderError, OSError, SMTPException):
            logger.exception("Could not send signup activation email.")
            messages.warning(
                self.request,
                "Konto utworzone, ale nie udało się wysłać maila aktywacyjnego. Spróbuj ponownie później albo skontaktuj się z nami.",
            )
        else:
            messages.success(
                self.request,
                "Konto utworzone. Wysłaliśmy link aktywacyjny na podany adres email.",
            )
        return super().form_valid(form)


class ActivationSentView(TemplateView):
    template_name = "accounts/activation_sent.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["site_name"] = "SaaS Home"
        context["page_title"] = "Potwierdź email"
        context["signup_email"] = self.request.session.get("signup_email")
        return context


def activate_account_view(request, uidb64, token):
    UserModel = get_user_model()
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = UserModel.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, UserModel.DoesNotExist):
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        messages.error(
            request,
            "Link aktywacyjny jest nieprawidłowy albo wygasł. Zarejestruj się ponownie lub skontaktuj się z nami.",
        )
        return redirect("accounts:login")

    user.is_active = True
    user.save(update_fields=["is_active"])
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.email_verified = True
    profile.save(update_fields=["email_verified", "updated_at"])
    login(request, user, backend="accounts.backends.EmailBackend")
    messages.success(request, "Email potwierdzony. Twoje konto jest aktywne.")
    return redirect("accounts:dashboard")


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
