from django.shortcuts import render


def home_view(request):
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
