from decimal import Decimal

from django.core.management.base import BaseCommand

from billing.models import Plan


class Command(BaseCommand):
    help = "Create or update default manual billing plans."

    def handle(self, *args, **options):
        plans = [
            {
                "slug": "free",
                "name": "Free",
                "description": "Startowy pakiet dla niezweryfikowanych lub testowych profili.",
                "price_monthly": Decimal("0"),
                "sort_order": 10,
                "can_view_leads": False,
                "can_view_analytics": False,
                "can_show_website": False,
                "can_show_documents": False,
                "can_be_verified": False,
                "can_be_promoted": False,
                "max_franchises": 1,
                "max_documents_per_franchise": None,
            },
            {
                "slug": "basic",
                "name": "Basic",
                "description": "Podstawowa obecność w katalogu z dostępem do leadów.",
                "price_monthly": Decimal("199"),
                "sort_order": 20,
                "can_view_leads": True,
                "can_view_analytics": False,
                "can_show_website": True,
                "can_show_documents": False,
                "can_be_verified": False,
                "can_be_promoted": False,
                "max_franchises": 1,
                "max_documents_per_franchise": None,
            },
            {
                "slug": "premium",
                "name": "Premium",
                "description": "Pełny pakiet widoczności, analityki i premium oznaczeń.",
                "price_monthly": Decimal("499"),
                "sort_order": 30,
                "can_view_leads": True,
                "can_view_analytics": True,
                "can_show_website": True,
                "can_show_documents": True,
                "can_be_verified": True,
                "can_be_promoted": True,
                "max_franchises": 3,
                "max_documents_per_franchise": None,
            },
            {
                "slug": "enterprise",
                "name": "Enterprise",
                "description": "Indywidualny pakiet dla większych sieci i grup franczyzowych.",
                "price_monthly": None,
                "sort_order": 40,
                "can_view_leads": True,
                "can_view_analytics": True,
                "can_show_website": True,
                "can_show_documents": True,
                "can_be_verified": True,
                "can_be_promoted": True,
                "max_franchises": None,
                "max_documents_per_franchise": None,
            },
        ]

        for plan_data in plans:
            slug = plan_data.pop("slug")
            Plan.objects.update_or_create(
                slug=slug,
                defaults={
                    **plan_data,
                    "currency": Plan.CURRENCY_PLN,
                    "is_active": True,
                },
            )
            self.stdout.write(self.style.SUCCESS(f"Seeded plan: {slug}"))
