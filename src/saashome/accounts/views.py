import logging
from smtplib import SMTPException

from django.contrib import messages
from django.contrib.auth import login
from django.conf import settings
from django.core.mail import BadHeaderError, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import FormView

from .forms import SignupForm


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
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        user = form.save()
        login(self.request, user)
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
