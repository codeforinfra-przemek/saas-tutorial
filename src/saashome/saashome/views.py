import logging
from smtplib import SMTPException

from django.conf import settings
from django.contrib import messages
from django.core.mail import BadHeaderError
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import render
from django.template.loader import render_to_string

from billing.services import apply_promotion_flags
from content.models import Article
from franchises.models import Franchise, FranchiseCategory

from .forms import ContactRequestForm


logger = logging.getLogger(__name__)


def send_contact_invitation_email(email):
    context = {
        "email": email,
        "site_name": "SaaS Home",
        "contact_email": settings.DEFAULT_FROM_EMAIL,
    }
    subject = "Dziekujemy za zainteresowanie SaaS Home"
    text_body = render_to_string("emails/contact_invitation.txt", context)
    html_body = render_to_string("emails/contact_invitation.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
        reply_to=[settings.DEFAULT_FROM_EMAIL],
    )
    message.attach_alternative(html_body, "text/html")
    message.send(fail_silently=False)


def home_view(request):
    contact_form = ContactRequestForm()
    ranked_franchises = apply_promotion_flags(
        Franchise.objects.filter(is_active=True).select_related("category", "organization")
    )
    premium_featured = [
        franchise
        for franchise in ranked_franchises
        if getattr(getattr(franchise, "subscription_plan", None), "can_feature_on_homepage", False)
    ]
    featured_franchises = (premium_featured or list(ranked_franchises))[:6]

    if request.method == "POST":
        contact_form = ContactRequestForm(request.POST)
        if contact_form.is_valid():
            email = contact_form.cleaned_data["email"]
            try:
                send_contact_invitation_email(email)
            except (BadHeaderError, OSError, SMTPException):
                logger.exception("Could not send contact invitation email.")
                messages.error(
                    request,
                    "Nie udalo sie wyslac maila. Sprobuj ponownie za chwile.",
                )
            else:
                messages.success(
                    request,
                    "Wyslalismy zaproszenie. Sprawdz skrzynke email.",
                )
                contact_form = ContactRequestForm()
        else:
            messages.error(
                request,
                "Sprawdz adres email i sprobuj ponownie.",
            )

    context = {
        "site_name": "SaaS Home",
        "page_title": "Strona główna",
        "active_page": "home",
        "eyebrow": "Ranking i porównywarka franczyz",
        "headline": "Porównaj franczyzy przed pierwszą rozmową.",
        "lead_text": "Sprawdź inwestycję, model biznesowy, skalę sieci i dostępne lokalizacje. Zapisz najlepsze opcje i wyślij zapytanie do wybranej marki.",
        "course_code_url": "https://github.com/codingforentrepreneurs/SaaS-Foundations",
        "my_code_url": "https://github.com/codeforinfra-przemek/saas-tutorial",
        "contact_form": contact_form,
        "featured_franchises": featured_franchises,
        "categories": FranchiseCategory.objects.filter(is_active=True)[:6],
        "latest_articles": Article.objects.published().select_related("category")[:3],
    }
    return render(request, "home.html", context)
