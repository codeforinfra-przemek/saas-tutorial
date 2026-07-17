from decimal import Decimal

from django.db import migrations


PLANS = (
    {
        "slug": "free",
        "name": "Free",
        "description": "Startowy pakiet dla niezweryfikowanych lub testowych profili.",
        "price_monthly": Decimal("0"),
        "price_yearly": Decimal("0"),
        "sort_order": 10,
        "max_description_length": 1200,
    },
    {
        "slug": "basic",
        "name": "Profil",
        "description": "Rozbudowany profil franczyzy z galerią, dokumentami, stroną WWW i leadami.",
        "price_monthly": Decimal("199"),
        "price_yearly": Decimal("1990"),
        "sort_order": 20,
        "can_view_leads": True,
        "can_show_website": True,
        "can_show_documents": True,
        "max_documents_per_franchise": 3,
        "max_gallery_images": 3,
        "max_description_length": 5000,
    },
    {
        "slug": "growth",
        "name": "Promocja",
        "description": "Profil, wyższa pozycja na liście, oznaczenie Promowane i analityka.",
        "price_monthly": Decimal("499"),
        "price_yearly": Decimal("4990"),
        "sort_order": 30,
        "can_view_leads": True,
        "can_view_analytics": True,
        "can_show_website": True,
        "can_show_documents": True,
        "can_be_promoted": True,
        "max_documents_per_franchise": 10,
        "max_gallery_images": 8,
        "max_description_length": 12000,
    },
    {
        "slug": "pro",
        "name": "Pro",
        "description": "Najwyższa widoczność, wyróżnienia, priorytetowe leady i wsparcie.",
        "price_monthly": Decimal("899"),
        "price_yearly": Decimal("8990"),
        "sort_order": 40,
        "can_view_leads": True,
        "can_view_analytics": True,
        "can_show_website": True,
        "can_show_documents": True,
        "can_be_verified": True,
        "can_be_promoted": True,
        "can_receive_priority_leads": True,
        "can_feature_in_category": True,
        "can_feature_on_homepage": True,
        "has_priority_support": True,
        "max_documents_per_franchise": 25,
        "max_gallery_images": 15,
        "max_description_length": 25000,
    },
)


def seed_plans(apps, schema_editor):
    plan_model = apps.get_model("billing", "Plan")
    for values in PLANS:
        defaults = {
            "currency": "PLN",
            "is_active": True,
            "can_view_leads": False,
            "can_view_analytics": False,
            "can_show_website": False,
            "can_show_documents": False,
            "can_be_verified": False,
            "can_be_promoted": False,
            "can_receive_priority_leads": False,
            "can_feature_in_category": False,
            "can_feature_on_homepage": False,
            "has_priority_support": False,
            "max_documents_per_franchise": 0,
            "max_gallery_images": 0,
            **values,
        }
        slug = defaults.pop("slug")
        plan_model.objects.update_or_create(slug=slug, defaults=defaults)
    plan_model.objects.filter(slug__in=("premium", "enterprise")).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("billing", "0003_plan_can_feature_in_category_and_more")]

    operations = [migrations.RunPython(seed_plans, migrations.RunPython.noop)]
