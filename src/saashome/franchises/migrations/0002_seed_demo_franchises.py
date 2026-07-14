from decimal import Decimal

from django.db import migrations


def seed_demo_franchises(apps, schema_editor):
    FranchiseCategory = apps.get_model("franchises", "FranchiseCategory")
    Franchise = apps.get_model("franchises", "Franchise")
    FranchiseLocation = apps.get_model("franchises", "FranchiseLocation")

    categories = {
        "gastronomia": {"name": "Gastronomia", "sort_order": 10},
        "edukacja": {"name": "Edukacja", "sort_order": 20},
        "uslugi": {"name": "Usługi", "sort_order": 30},
        "zdrowie-i-fitness": {"name": "Zdrowie i fitness", "sort_order": 40},
    }
    category_objects = {}
    for slug, data in categories.items():
        category, _ = FranchiseCategory.objects.update_or_create(
            slug=slug,
            defaults={
                "name": data["name"],
                "sort_order": data["sort_order"],
                "is_active": True,
            },
        )
        category_objects[slug] = category

    franchises = [
        {
            "slug": "green-bowl",
            "category": "gastronomia",
            "name": "Green Bowl",
            "short_description": "Sieć zdrowych bowl barów w lokalizacjach biurowych i galeriach handlowych.",
            "description": "Green Bowl to lekki format gastronomiczny oparty o zdrowe miski, szybki serwis i powtarzalne menu. Model jest przygotowany dla lokali 35-70 m2 oraz punktów typu food court.",
            "website_url": "https://example.com/green-bowl",
            "min_investment": Decimal("180000"),
            "max_investment": Decimal("420000"),
            "initial_fee": Decimal("45000"),
            "royalty_fee_text": "6% przychodu miesięcznie",
            "marketing_fee_text": "2% przychodu miesięcznie",
            "business_type": "stationary",
            "required_premises": "35-70 m2, wysoki ruch pieszy",
            "home_based": False,
            "part_time_possible": False,
            "training_provided": True,
            "financing_available": True,
            "founded_year": 2017,
            "franchising_since": 2021,
            "total_units": 42,
            "poland_units": 18,
            "rank_score": Decimal("91.50"),
            "popularity_score": Decimal("86.00"),
            "editor_rating": Decimal("4.60"),
            "is_verified": True,
            "is_promoted": True,
            "is_featured": True,
        },
        {
            "slug": "math-lab-kids",
            "category": "edukacja",
            "name": "Math Lab Kids",
            "short_description": "Zajęcia matematyczno-logiczne dla dzieci w wieku 6-14 lat.",
            "description": "Math Lab Kids oferuje gotowy program zajęć, materiały dydaktyczne i szkolenia dla franczyzobiorców. Format można prowadzić w małym lokalu edukacyjnym lub w modelu partnerskim ze szkołami.",
            "website_url": "https://example.com/math-lab-kids",
            "min_investment": Decimal("55000"),
            "max_investment": Decimal("140000"),
            "initial_fee": Decimal("25000"),
            "royalty_fee_text": "stała opłata od aktywnej grupy",
            "marketing_fee_text": "900 zł miesięcznie",
            "business_type": "hybrid",
            "required_premises": "20-45 m2 lub sale partnerskie",
            "home_based": True,
            "part_time_possible": True,
            "training_provided": True,
            "financing_available": False,
            "founded_year": 2015,
            "franchising_since": 2019,
            "total_units": 68,
            "poland_units": 52,
            "rank_score": Decimal("88.20"),
            "popularity_score": Decimal("80.50"),
            "editor_rating": Decimal("4.40"),
            "is_verified": True,
            "is_promoted": False,
            "is_featured": True,
        },
        {
            "slug": "fit24-studio",
            "category": "zdrowie-i-fitness",
            "name": "Fit24 Studio",
            "short_description": "Kameralne studio treningu personalnego i EMS dla osiedli premium.",
            "description": "Fit24 Studio skupia się na małych, rentownych lokalach z usługami treningu personalnego, EMS i konsultacji dietetycznych. Model zakłada mocne wsparcie operacyjne oraz centralny marketing online.",
            "website_url": "https://example.com/fit24-studio",
            "min_investment": Decimal("220000"),
            "max_investment": Decimal("520000"),
            "initial_fee": Decimal("60000"),
            "royalty_fee_text": "7% przychodu",
            "marketing_fee_text": "2 500 zł miesięcznie",
            "business_type": "stationary",
            "required_premises": "60-120 m2, parter lub wejście z ulicy",
            "home_based": False,
            "part_time_possible": False,
            "training_provided": True,
            "financing_available": True,
            "founded_year": 2018,
            "franchising_since": 2022,
            "total_units": 24,
            "poland_units": 14,
            "rank_score": Decimal("84.75"),
            "popularity_score": Decimal("78.00"),
            "editor_rating": Decimal("4.20"),
            "is_verified": True,
            "is_promoted": False,
            "is_featured": False,
        },
        {
            "slug": "parcel-point-pro",
            "category": "uslugi",
            "name": "Parcel Point Pro",
            "short_description": "Punkt usług kurierskich, druku i obsługi zwrotów dla lokalnych społeczności.",
            "description": "Parcel Point Pro łączy obsługę przesyłek, zwrotów e-commerce, podstawowe usługi druku oraz lokalną reklamę. To prosty format usługowy dla lokali przy osiedlach i biurowcach.",
            "website_url": "https://example.com/parcel-point-pro",
            "min_investment": Decimal("70000"),
            "max_investment": Decimal("160000"),
            "initial_fee": Decimal("30000"),
            "royalty_fee_text": "1 800 zł miesięcznie",
            "marketing_fee_text": "500 zł miesięcznie",
            "business_type": "stationary",
            "required_premises": "25-55 m2",
            "home_based": False,
            "part_time_possible": True,
            "training_provided": True,
            "financing_available": False,
            "founded_year": 2014,
            "franchising_since": 2018,
            "total_units": 118,
            "poland_units": 91,
            "rank_score": Decimal("82.10"),
            "popularity_score": Decimal("74.00"),
            "editor_rating": Decimal("4.05"),
            "is_verified": False,
            "is_promoted": False,
            "is_featured": False,
        },
    ]

    location_data = {
        "green-bowl": [
            ("Headquarters", "Warszawa", "Mazowieckie", "headquarters", Decimal("52.229676"), Decimal("21.012229")),
            ("Green Bowl Kraków", "Kraków", "Małopolskie", "existing_unit", Decimal("50.064650"), Decimal("19.944980")),
            ("Obszar rozwoju Wrocław", "Wrocław", "Dolnośląskie", "available_area", Decimal("51.107883"), Decimal("17.038538")),
        ],
        "math-lab-kids": [
            ("Math Lab Kids Poznań", "Poznań", "Wielkopolskie", "existing_unit", Decimal("52.406374"), Decimal("16.925168")),
            ("Obszar rozwoju Gdańsk", "Gdańsk", "Pomorskie", "available_area", Decimal("54.352025"), Decimal("18.646638")),
        ],
        "fit24-studio": [
            ("Fit24 Studio Warszawa Mokotów", "Warszawa", "Mazowieckie", "existing_unit", Decimal("52.190760"), Decimal("21.012230")),
            ("Obszar rozwoju Łódź", "Łódź", "Łódzkie", "available_area", Decimal("51.759248"), Decimal("19.455983")),
        ],
        "parcel-point-pro": [
            ("Parcel Point Pro Katowice", "Katowice", "Śląskie", "existing_unit", Decimal("50.264892"), Decimal("19.023782")),
            ("Obszar rozwoju Lublin", "Lublin", "Lubelskie", "available_area", Decimal("51.246452"), Decimal("22.568445")),
        ],
    }

    for data in franchises:
        slug = data.pop("slug")
        category_slug = data.pop("category")
        franchise, _ = Franchise.objects.update_or_create(
            slug=slug,
            defaults={
                **data,
                "category": category_objects[category_slug],
                "is_active": True,
            },
        )

        for name, city, region, location_type, latitude, longitude in location_data[slug]:
            FranchiseLocation.objects.update_or_create(
                franchise=franchise,
                name=name,
                city=city,
                defaults={
                    "region": region,
                    "location_type": location_type,
                    "address": "",
                    "latitude": latitude,
                    "longitude": longitude,
                    "is_active": True,
                },
            )


def unseed_demo_franchises(apps, schema_editor):
    Franchise = apps.get_model("franchises", "Franchise")
    FranchiseCategory = apps.get_model("franchises", "FranchiseCategory")
    Franchise.objects.filter(
        slug__in=["green-bowl", "math-lab-kids", "fit24-studio", "parcel-point-pro"]
    ).delete()
    FranchiseCategory.objects.filter(
        slug__in=["gastronomia", "edukacja", "uslugi", "zdrowie-i-fitness"]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("franchises", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_demo_franchises, unseed_demo_franchises),
    ]
