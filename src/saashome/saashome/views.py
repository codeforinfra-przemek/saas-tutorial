from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import render
from django.template.loader import render_to_string

from .forms import ContactRequestForm


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

    if request.method == "POST":
        contact_form = ContactRequestForm(request.POST)
        if contact_form.is_valid():
            email = contact_form.cleaned_data["email"]
            send_contact_invitation_email(email)
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
        "eyebrow": "Pierwsza strona projektu Django",
        "headline": "Lista obiektów z mapą w jednym miejscu.",
        "lead_text": (
            "Tymczasowa strona główna dla naszego SaaS-a. Na razie jest "
            "statyczna, ale daje kierunek: wyszukiwarka, lista obiektów "
            "i mapa lokalizacji."
        ),
        "course_code_url": "https://github.com/codingforentrepreneurs/SaaS-Foundations",
        "my_code_url": "https://github.com/codeforinfra-przemek/saas-tutorial",
        "contact_form": contact_form,
        "featured_objects": [
            {
                "name": "Studio coworkingowe",
                "city": "Warszawa",
                "note": "dostępne od zaraz",
            },
            {
                "name": "Magazyn miejski",
                "city": "Kraków",
                "note": "świetna komunikacja",
            },
            {
                "name": "Lokal usługowy",
                "city": "Wrocław",
                "note": "centrum miasta",
            },
        ],
        "roadmap": [
            {
                "step": "01",
                "label": "Django jako fundament aplikacji",
            },
            {
                "step": "02",
                "label": "Obiekty, filtrowanie i szczegóły",
            },
            {
                "step": "03",
                "label": "Mapa lokalizacji w kolejnym kroku",
            },
        ],
    }
    return render(request, "home.html", context)
