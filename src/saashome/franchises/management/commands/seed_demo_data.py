from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Organization, OrganizationMembership, UserProfile
from billing.models import FranchisePromotion, OrganizationSubscription, Plan
from content.models import Article, ArticleCategory, LandingPage
from franchises.models import Franchise, FranchiseCategory, FranchiseLocation
from franchises.reference_data import mcdonalds_reference_fields
from leads.models import Lead
from visits.models import Visit, VisitEvent


class Command(BaseCommand):
    help = "Seed richer demo data for the franchise SaaS MVP."

    def add_arguments(self, parser):
        parser.add_argument(
            "--vendor-username",
            default="",
            help="Assign this existing user as owner of the demo vendor organizations.",
        )

    def handle(self, *args, **options):
        call_command("seed_plans")
        categories = self.seed_categories()
        organizations = self.seed_organizations()
        franchises = self.seed_franchises(categories, organizations)
        self.seed_locations(franchises)
        self.seed_subscriptions(organizations)
        self.seed_promotions(franchises)
        self.seed_leads(franchises)
        self.seed_visits(franchises)
        self.seed_content(categories, franchises)
        self.seed_vendor_memberships(organizations, options["vendor_username"])
        self.stdout.write(self.style.SUCCESS("Demo data seeded."))

    def seed_vendor_memberships(self, organizations, username):
        if not username:
            self.stdout.write(
                self.style.WARNING(
                    "No vendor membership created. Run with --vendor-username YOUR_USERNAME to populate Vendor Dashboard."
                )
            )
            return

        user = get_user_model().objects.filter(username=username).first()
        if not user:
            self.stdout.write(self.style.WARNING(f'User "{username}" was not found; vendor memberships were skipped.'))
            return

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if profile.user_type != UserProfile.USER_TYPE_VENDOR:
            profile.user_type = UserProfile.USER_TYPE_VENDOR
            profile.save(update_fields=["user_type", "updated_at"])

        for organization in organizations.values():
            OrganizationMembership.objects.update_or_create(
                organization=organization,
                user=user,
                defaults={"role": OrganizationMembership.ROLE_OWNER, "is_active": True},
            )
        self.stdout.write(
            self.style.SUCCESS(
                f'Assigned {user.username} as owner of {len(organizations)} demo vendor organizations.'
            )
        )

    def seed_categories(self):
        data = [
            ("convenience", "Sklepy convenience", 10),
            ("gastronomia", "Gastronomia", 20),
            ("fitness", "Fitness", 30),
            ("edukacja", "Edukacja", 40),
            ("uslugi", "Usługi", 50),
        ]
        categories = {}
        for slug, name, sort_order in data:
            category, _ = FranchiseCategory.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "sort_order": sort_order, "is_active": True},
            )
            categories[slug] = category
        return categories

    def seed_organizations(self):
        data = [
            ("zabka-polska-demo", "Żabka Polska Demo"),
            ("carrefour-express-demo", "Carrefour Express Demo"),
            ("mcdonalds-demo", "McDonald's Demo"),
            ("amrest-demo", "AmRest Demo"),
            ("subway-demo", "Subway Demo"),
            ("dominospizza-demo", "Domino's Pizza Demo"),
            ("dagrasso-demo", "Da Grasso Demo"),
            ("northfish-demo", "North Fish Demo"),
            ("zahir-kebab-demo", "Zahir Kebab Demo"),
            ("xtreme-fitness-demo", "Xtreme Fitness Gyms Demo"),
        ]
        organizations = {}
        for slug, name in data:
            organization, _ = Organization.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "website_url": "https://example.com",
                    "contact_email": f"{slug}@example.com",
                    "billing_email": f"billing-{slug}@example.com",
                    "status": Organization.STATUS_ACTIVE,
                    "package_type": Organization.PACKAGE_PREMIUM if "demo" in slug else Organization.PACKAGE_BASIC,
                    "description": "Demo organization for MVP presentation data.",
                },
            )
            organizations[slug] = organization
        return organizations

    def seed_franchises(self, categories, organizations):
        data = [
            {
                "slug": "zabka",
                "name": "Żabka",
                "category": "convenience",
                "organization": "zabka-polska-demo",
                "short_description": "Sieć sklepów convenience z bardzo rozpoznawalnym formatem miejskim.",
                "description": "Żabka to jedna z najbardziej rozpoznawalnych sieci convenience w Polsce. Demo profil pokazuje model sklepu blisko klienta, krótkie zakupy, mocną markę i wysoki nacisk na lokalizację. Dane finansowe w tym seedzie są przykładowe do testów MVP.",
                "website_url": "https://www.zabka.pl/franczyza",
                "min_investment": "5000",
                "max_investment": "80000",
                "initial_fee": "5000",
                "royalty_fee_text": "model agencyjny/franczyzowy, zależny od umowy",
                "marketing_fee_text": "wg aktualnych warunków sieci",
                "business_type": Franchise.BUSINESS_TYPE_STATIONARY,
                "required_premises": "lokal handlowy w ruchliwej lokalizacji",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": True,
                "founded_year": 1998,
                "franchising_since": 2000,
                "total_units": 10000,
                "poland_units": 10000,
                "rank_score": "97.50",
                "popularity_score": "98.00",
                "editor_rating": "4.80",
                "is_verified": True,
                "is_promoted": True,
                "is_featured": True,
            },
            {
                "slug": "carrefour-express",
                "name": "Carrefour Express",
                "category": "convenience",
                "organization": "carrefour-express-demo",
                "short_description": "Franczyzowy format sklepu spożywczego pod globalną marką retail.",
                "description": "Carrefour Express to mały format handlowy funkcjonujący w modelu franczyzowym. Profil demo pokazuje ofertę dla przedsiębiorców zainteresowanych lokalnym sklepem spożywczym z zapleczem dużej sieci.",
                "website_url": "https://www.carrefour.pl/franczyza",
                "min_investment": "80000",
                "max_investment": "350000",
                "initial_fee": "15000",
                "royalty_fee_text": "wg umowy franczyzowej",
                "marketing_fee_text": "wg pakietu i formatu",
                "business_type": Franchise.BUSINESS_TYPE_STATIONARY,
                "required_premises": "sklep osiedlowy lub lokal convenience",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": True,
                "founded_year": 1959,
                "franchising_since": 2009,
                "total_units": 14000,
                "poland_units": 900,
                "rank_score": "90.20",
                "popularity_score": "84.50",
                "editor_rating": "4.30",
                "is_verified": True,
                "is_promoted": False,
                "is_featured": False,
            },
            {
                "slug": "mcdonalds",
                "name": "McDonald's",
                "category": "gastronomia",
                "organization": "mcdonalds-demo",
                "short_description": "Globalna marka restauracji quick service z bardzo wysokim progiem wejścia.",
                "description": "McDonald's to globalny standard franczyzy gastronomicznej. Ten demo profil pomaga porównywać formaty o dużej rozpoznawalności, skali operacyjnej i wysokich wymaganiach inwestycyjnych.",
                "website_url": "https://mcdonalds.pl/o-mcdonalds/franczyza/",
                "min_investment": "1200000",
                "max_investment": "4500000",
                "initial_fee": "180000",
                "royalty_fee_text": "opłaty licencyjne i czynsz wg umowy",
                "marketing_fee_text": "wkład marketingowy wg systemu",
                "business_type": Franchise.BUSINESS_TYPE_STATIONARY,
                "required_premises": "restauracja wolnostojąca, galeria lub drive-thru",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": False,
                "founded_year": 1955,
                "franchising_since": 1955,
                "total_units": 40000,
                "poland_units": 550,
                "rank_score": "96.40",
                "popularity_score": "97.00",
                "editor_rating": "4.70",
                "is_verified": True,
                "is_promoted": True,
                "is_featured": True,
            },
            {
                "slug": "kfc",
                "name": "KFC",
                "category": "gastronomia",
                "organization": "amrest-demo",
                "short_description": "Sieć restauracji quick service znana z kurczaka i dużej skali operacyjnej.",
                "description": "KFC to międzynarodowa marka gastronomiczna działająca w wielu krajach. Profil demo służy do testowania segmentu QSR i porównania z innymi formatami food service.",
                "website_url": "https://kfc.pl",
                "min_investment": "900000",
                "max_investment": "3200000",
                "initial_fee": "150000",
                "royalty_fee_text": "wg warunków master/operatora",
                "marketing_fee_text": "wkład marketingowy wg sieci",
                "business_type": Franchise.BUSINESS_TYPE_STATIONARY,
                "required_premises": "lokal restauracyjny lub food court",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": False,
                "founded_year": 1952,
                "franchising_since": 1952,
                "total_units": 30000,
                "poland_units": 300,
                "rank_score": "92.00",
                "popularity_score": "93.50",
                "editor_rating": "4.40",
                "is_verified": True,
                "is_promoted": False,
                "is_featured": True,
            },
            {
                "slug": "pizza-hut",
                "name": "Pizza Hut",
                "category": "gastronomia",
                "organization": "amrest-demo",
                "short_description": "Międzynarodowa marka pizzy z formatami restauracyjnymi i delivery.",
                "description": "Pizza Hut to rozpoznawalna marka gastronomiczna działająca w różnych formatach. Demo profil pozwala testować karty z pizzą, delivery i lokalami w galeriach.",
                "website_url": "https://pizzahut.pl",
                "min_investment": "650000",
                "max_investment": "2200000",
                "initial_fee": "120000",
                "royalty_fee_text": "wg umowy franczyzowej/operatora",
                "marketing_fee_text": "wkład marketingowy wg systemu",
                "business_type": Franchise.BUSINESS_TYPE_HYBRID,
                "required_premises": "restauracja, delivery unit lub food court",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": False,
                "founded_year": 1958,
                "franchising_since": 1959,
                "total_units": 19000,
                "poland_units": 150,
                "rank_score": "88.60",
                "popularity_score": "86.00",
                "editor_rating": "4.10",
                "is_verified": True,
                "is_promoted": False,
                "is_featured": False,
            },
            {
                "slug": "subway",
                "name": "Subway",
                "category": "gastronomia",
                "organization": "subway-demo",
                "short_description": "Globalna sieć sandwich barów z relatywnie elastycznym formatem lokalu.",
                "description": "Subway to globalna sieć restauracji kanapkowych. Profil demo pokazuje format oparty o powtarzalne menu, szybki serwis i możliwość działania w mniejszych lokalach.",
                "website_url": "https://www.subway.com",
                "min_investment": "350000",
                "max_investment": "900000",
                "initial_fee": "60000",
                "royalty_fee_text": "opłata franczyzowa wg systemu",
                "marketing_fee_text": "fundusz marketingowy wg systemu",
                "business_type": Franchise.BUSINESS_TYPE_STATIONARY,
                "required_premises": "lokal gastronomiczny, street lub food court",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": True,
                "founded_year": 1965,
                "franchising_since": 1974,
                "total_units": 37000,
                "poland_units": 120,
                "rank_score": "84.40",
                "popularity_score": "82.00",
                "editor_rating": "4.00",
                "is_verified": True,
                "is_promoted": False,
                "is_featured": False,
            },
            {
                "slug": "dominos-pizza",
                "name": "Domino's Pizza",
                "category": "gastronomia",
                "organization": "dominospizza-demo",
                "short_description": "Sieć pizzy z mocnym naciskiem na delivery i operacyjną powtarzalność.",
                "description": "Domino's Pizza to marka oparta o delivery, technologię zamówień i procesową obsługę kuchni. Dane w profilu są demo do prezentacji marketplace'u franczyz.",
                "website_url": "https://www.dominospizza.pl",
                "min_investment": "500000",
                "max_investment": "1400000",
                "initial_fee": "90000",
                "royalty_fee_text": "wg umowy franczyzowej/operatora",
                "marketing_fee_text": "fundusz marketingowy wg sieci",
                "business_type": Franchise.BUSINESS_TYPE_HYBRID,
                "required_premises": "lokal delivery lub restauracyjny",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": False,
                "founded_year": 1960,
                "franchising_since": 1967,
                "total_units": 20000,
                "poland_units": 80,
                "rank_score": "83.50",
                "popularity_score": "80.20",
                "editor_rating": "4.00",
                "is_verified": True,
                "is_promoted": False,
                "is_featured": False,
            },
            {
                "slug": "da-grasso",
                "name": "Da Grasso",
                "category": "gastronomia",
                "organization": "dagrasso-demo",
                "short_description": "Polska sieć pizzerii działająca w modelu franczyzowym.",
                "description": "Da Grasso to polska marka pizzerii obecna w wielu miastach. Demo profil pokazuje lokalny format gastronomiczny z delivery, salą konsumpcyjną i rozpoznawalnością krajową.",
                "website_url": "https://www.dagrasso.pl/franczyza",
                "min_investment": "250000",
                "max_investment": "750000",
                "initial_fee": "50000",
                "royalty_fee_text": "wg aktualnych warunków sieci",
                "marketing_fee_text": "wg aktualnych warunków sieci",
                "business_type": Franchise.BUSINESS_TYPE_HYBRID,
                "required_premises": "lokal gastronomiczny z kuchnią i dostawami",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": True,
                "founded_year": 1996,
                "franchising_since": 1998,
                "total_units": 200,
                "poland_units": 200,
                "rank_score": "82.80",
                "popularity_score": "78.30",
                "editor_rating": "3.90",
                "is_verified": True,
                "is_promoted": False,
                "is_featured": False,
            },
            {
                "slug": "north-fish",
                "name": "North Fish",
                "category": "gastronomia",
                "organization": "northfish-demo",
                "short_description": "Polska sieć restauracji rybnych, często w lokalizacjach galeryjnych.",
                "description": "North Fish to koncept gastronomiczny oparty o dania rybne i szybki serwis. Profil demo rozszerza katalog o segment specjalistycznego food court/QSR.",
                "website_url": "https://northfish.pl",
                "min_investment": "400000",
                "max_investment": "1200000",
                "initial_fee": "70000",
                "royalty_fee_text": "wg warunków sieci",
                "marketing_fee_text": "wg warunków sieci",
                "business_type": Franchise.BUSINESS_TYPE_STATIONARY,
                "required_premises": "food court lub restauracja w centrum handlowym",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": False,
                "founded_year": 2002,
                "franchising_since": 2010,
                "total_units": 50,
                "poland_units": 45,
                "rank_score": "78.80",
                "popularity_score": "70.00",
                "editor_rating": "3.80",
                "is_verified": False,
                "is_promoted": False,
                "is_featured": False,
            },
            {
                "slug": "zahir-kebab",
                "name": "Zahir Kebab",
                "category": "gastronomia",
                "organization": "zahir-kebab-demo",
                "short_description": "Polska sieć kebabów z formatem lokalu street food i delivery.",
                "description": "Zahir Kebab to sieć gastronomiczna rozwijana w Polsce. Demo profil pokazuje popularny segment street food, lokal mniejszy niż pełna restauracja i potencjał delivery.",
                "website_url": "https://zahirkebab.pl/franczyza",
                "min_investment": "180000",
                "max_investment": "550000",
                "initial_fee": "45000",
                "royalty_fee_text": "wg aktualnych warunków franczyzy",
                "marketing_fee_text": "wg aktualnych warunków franczyzy",
                "business_type": Franchise.BUSINESS_TYPE_HYBRID,
                "required_premises": "lokal gastronomiczny 40-100 m2",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": True,
                "founded_year": 2014,
                "franchising_since": 2018,
                "total_units": 80,
                "poland_units": 80,
                "rank_score": "80.10",
                "popularity_score": "81.00",
                "editor_rating": "3.90",
                "is_verified": False,
                "is_promoted": True,
                "is_featured": False,
            },
            {
                "slug": "xtreme-fitness-gyms",
                "name": "Xtreme Fitness Gyms",
                "category": "fitness",
                "organization": "xtreme-fitness-demo",
                "short_description": "Polska sieć klubów fitness rozwijana w modelu franczyzowym.",
                "description": "Xtreme Fitness Gyms to marka klubów fitness obecna na polskim rynku. Demo profil pozwala porównać inwestycję w fitness z gastronomią i sklepami convenience.",
                "website_url": "https://franczyza.xtremefitness.pl",
                "min_investment": "700000",
                "max_investment": "2500000",
                "initial_fee": "90000",
                "royalty_fee_text": "wg aktualnych warunków sieci",
                "marketing_fee_text": "wg aktualnych warunków sieci",
                "business_type": Franchise.BUSINESS_TYPE_STATIONARY,
                "required_premises": "duży lokal usługowy lub retail park",
                "home_based": False,
                "part_time_possible": False,
                "training_provided": True,
                "financing_available": True,
                "founded_year": 2012,
                "franchising_since": 2017,
                "total_units": 70,
                "poland_units": 70,
                "rank_score": "85.30",
                "popularity_score": "79.00",
                "editor_rating": "4.10",
                "is_verified": True,
                "is_promoted": False,
                "is_featured": True,
            },
        ]
        data.extend(self.additional_demo_franchises())
        # The financial and network values below are deliberately marked as demo.
        # They make the MVP usable without presenting illustrative figures as a brand disclosure.
        for index, item in enumerate(data):
            if item["slug"] == "mcdonalds":
                item.update(mcdonalds_reference_fields())
                continue

            min_investment = Decimal(item["min_investment"])
            max_investment = Decimal(item["max_investment"])
            poland_units = item["poland_units"]
            total_units = item["total_units"]
            opened = max(1, poland_units // (24 + index * 2))
            closed = max(0, opened // 5)
            mature_revenue = max_investment * Decimal("4.6")
            item.update(
                {
                    "franchised_units": max(1, int(total_units * 0.82)),
                    "company_owned_units": max(0, total_units - int(total_units * 0.82)),
                    "units_opened_last_year": opened,
                    "units_closed_last_year": closed,
                    "units_transferred_last_year": max(0, opened // 3),
                    "unit_growth_percent_1y": (Decimal(opened * 100) / Decimal(max(1, poland_units - opened))).quantize(Decimal("0.01")),
                    "liquid_capital_required": (min_investment * Decimal("0.30")).quantize(Decimal("1")),
                    "net_worth_required": (max_investment * Decimal("0.65")).quantize(Decimal("1")),
                    "franchise_term_years": 5,
                    "renewal_term_years": 5,
                    "estimated_payback_months": 18 + (index % 6) * 6,
                    "mature_unit_revenue_annual": mature_revenue.quantize(Decimal("1")),
                    "mature_unit_operating_profit_annual": (mature_revenue * Decimal("0.14")).quantize(Decimal("1")),
                    "mature_unit_count": max(5, poland_units // 8),
                    "typical_unit_size_min_sqm": 30 + (index % 5) * 15,
                    "typical_unit_size_max_sqm": 80 + (index % 5) * 40,
                    "typical_staff_count": 4 + (index % 5) * 3,
                    "territory_type": Franchise.TERRITORY_PROTECTED if index % 2 else Franchise.TERRITORY_NOT_DISCLOSED,
                    "financial_performance_disclosed": True,
                    "financial_performance_note": "Dane demonstracyjne inspirowane zakresem Item 19 FDD. Przed decyzją inwestycyjną potwierdź metodykę, liczbę placówek w próbie i aktualność danych.",
                    "financial_data_as_of": date(2026, 6, 30),
                    "data_status": Franchise.DATA_STATUS_DEMO,
                    "data_source_url": "",
                }
            )

        franchises = {}
        for item in data:
            slug = item["slug"]
            defaults = {
                "name": item["name"],
                "category": categories[item["category"]],
                "organization": organizations.get(item["organization"]) if item.get("organization") else None,
                "short_description": item["short_description"],
                "description": item["description"],
                "website_url": item["website_url"],
                "min_investment": Decimal(item["min_investment"]) if item["min_investment"] is not None else None,
                "max_investment": Decimal(item["max_investment"]) if item["max_investment"] is not None else None,
                "initial_fee": Decimal(item["initial_fee"]) if item["initial_fee"] is not None else None,
                "royalty_fee_text": item["royalty_fee_text"],
                "marketing_fee_text": item["marketing_fee_text"],
                "business_type": item["business_type"],
                "required_premises": item["required_premises"],
                "home_based": item["home_based"],
                "part_time_possible": item["part_time_possible"],
                "training_provided": item["training_provided"],
                "financing_available": item["financing_available"],
                "founded_year": item["founded_year"],
                "franchising_since": item["franchising_since"],
                "total_units": item["total_units"],
                "poland_units": item["poland_units"],
                "franchised_units": item["franchised_units"],
                "company_owned_units": item["company_owned_units"],
                "units_opened_last_year": item["units_opened_last_year"],
                "units_closed_last_year": item["units_closed_last_year"],
                "units_transferred_last_year": item["units_transferred_last_year"],
                "unit_growth_percent_1y": item["unit_growth_percent_1y"],
                "liquid_capital_required": item["liquid_capital_required"],
                "net_worth_required": item["net_worth_required"],
                "franchise_term_years": item["franchise_term_years"],
                "renewal_term_years": item["renewal_term_years"],
                "estimated_payback_months": item["estimated_payback_months"],
                "mature_unit_revenue_annual": item["mature_unit_revenue_annual"],
                "mature_unit_operating_profit_annual": item["mature_unit_operating_profit_annual"],
                "mature_unit_count": item["mature_unit_count"],
                "typical_unit_size_min_sqm": item["typical_unit_size_min_sqm"],
                "typical_unit_size_max_sqm": item["typical_unit_size_max_sqm"],
                "typical_staff_count": item["typical_staff_count"],
                "territory_type": item["territory_type"],
                "financial_performance_disclosed": item["financial_performance_disclosed"],
                "financial_performance_note": item["financial_performance_note"],
                "financial_data_as_of": item["financial_data_as_of"],
                "data_status": item["data_status"],
                "data_source_url": item["data_source_url"],
                "rank_score": Decimal(item["rank_score"]),
                "popularity_score": Decimal(item["popularity_score"]),
                "editor_rating": Decimal(item["editor_rating"]),
                "is_verified": item["is_verified"],
                "is_promoted": item["is_promoted"],
                "is_featured": item["is_featured"],
                "is_active": True,
            }
            franchise, _ = Franchise.objects.update_or_create(slug=slug, defaults=defaults)
            franchises[slug] = franchise
        return franchises

    def additional_demo_franchises(self):
        """Real brand examples with illustrative MVP figures, not commercial offers."""
        specs = [
            ("abc", "abc", "convenience", "https://www.sklepyabc.pl", "Sieć niezależnych sklepów spożywczych."),
            ("groszek", "Groszek", "convenience", "https://www.groszek.com.pl", "Format sklepu spożywczego dla lokalnych przedsiębiorców."),
            ("lewiatan", "Lewiatan", "convenience", "https://www.lewiatan.pl", "Sieć sklepów działających pod wspólną marką."),
            ("delikatesy-centrum", "Delikatesy Centrum", "convenience", "https://www.delikatesycentrum.pl", "Format supermarketu i sklepu osiedlowego."),
            ("chorten", "Chorten", "convenience", "https://chorten.pl", "Sieć sklepów spożywczych o lokalnym charakterze."),
            ("lodolandia", "Lodolandia", "gastronomia", "https://lodolandia.pl", "Sezonowy format street food z lodami i goframi."),
            ("bafra-kebab", "Bafra Kebab", "gastronomia", "https://franczyza.bafrakebab.pl", "Format szybkiej gastronomii z obsługą na miejscu i na wynos."),
            ("fit-cake", "Fit Cake", "gastronomia", "https://fitcake.pl", "Kawiarniany koncept z ofertą bez cukru i dietetyczną."),
            ("makarun", "Makarun", "gastronomia", "https://makarun.pl", "Szybka gastronomia oparta o dania makaronowe."),
            ("crazy-bubble", "Crazy Bubble", "gastronomia", "https://crazybubble.pl", "Punkt napojów bubble tea w formacie retail i food court."),
            ("kolacz-na-okraglo", "Kołacz na Okrągło", "gastronomia", "https://kolaczonakraglo.pl", "Punkt street food z ofertą słodkich wypieków."),
            ("lody-bonano", "Lody Bonano", "gastronomia", "https://lodybonano.pl", "Sezonowy punkt gastronomiczny z lodami i deserami."),
            ("early-stage", "Early Stage", "edukacja", "https://franczyza.earlystage.pl", "Szkoła języka angielskiego rozwijana w modelu franczyzowym."),
            ("helen-doron", "Helen Doron English", "edukacja", "https://www.helendoron.pl", "Edukacyjny format nauki języka angielskiego dla dzieci."),
            ("mathriders", "MathRiders", "edukacja", "https://mathriders.pl", "Sieć zajęć matematycznych dla dzieci."),
            ("edukido", "Edukido", "edukacja", "https://edukido.com.pl", "Mobilny format zajęć edukacyjnych bez stałego lokalu."),
            ("depilconcept", "DepilConcept", "uslugi", "https://depilconcept.pl", "Salon usług beauty w modelu sieciowym."),
            ("yasumi", "YASUMI", "uslugi", "https://yasumi.pl", "Gabinet kosmetyczny i spa w formacie usługowym."),
            ("5asec", "5 a sec", "uslugi", "https://www.5asec.pl", "Usługi pralnicze dla klientów indywidualnych i biznesowych."),
            ("36-minut", "36 MINUT", "fitness", "https://36minut.pl", "Kameralny format treningowy i klub fitness."),
        ]
        category_defaults = {
            "convenience": (90000, 380000, 14000, 130, Franchise.BUSINESS_TYPE_STATIONARY),
            "gastronomia": (120000, 850000, 35000, 95, Franchise.BUSINESS_TYPE_HYBRID),
            "edukacja": (30000, 180000, 5000, 80, Franchise.BUSINESS_TYPE_HYBRID),
            "uslugi": (180000, 720000, 40000, 55, Franchise.BUSINESS_TYPE_STATIONARY),
            "fitness": (550000, 1600000, 75000, 45, Franchise.BUSINESS_TYPE_STATIONARY),
        }
        profiles = []
        for index, (slug, name, category, website_url, short_description) in enumerate(specs):
            min_investment, max_investment, initial_fee, poland_units, business_type = category_defaults[category]
            profiles.append(
                {
                    "slug": slug,
                    "name": name,
                    "category": category,
                    "organization": None,
                    "short_description": short_description,
                    "description": (
                        f"{name} to przykład realnie istniejącej marki użyty do rozbudowy katalogu MVP. "
                        "Opis, wartości inwestycyjne, wskaźniki sieci i lokalizacje w tym profilu są demonstracyjne "
                        "i nie stanowią oferty franczyzodawcy."
                    ),
                    "website_url": website_url,
                    "min_investment": str(min_investment),
                    "max_investment": str(max_investment),
                    "initial_fee": str(initial_fee),
                    "royalty_fee_text": "dane demonstracyjne - sprawdź warunki marki",
                    "marketing_fee_text": "dane demonstracyjne - sprawdź warunki marki",
                    "business_type": business_type,
                    "required_premises": "format i lokalizacja do potwierdzenia z franczyzodawcą",
                    "home_based": category == "edukacja",
                    "part_time_possible": category == "edukacja",
                    "training_provided": True,
                    "financing_available": category in {"convenience", "gastronomia", "fitness"},
                    "founded_year": None,
                    "franchising_since": None,
                    "total_units": poland_units,
                    "poland_units": poland_units,
                    "rank_score": str(72 + (index % 16)),
                    "popularity_score": str(65 + (index % 20)),
                    "editor_rating": "0.00",
                    "is_verified": False,
                    "is_promoted": False,
                    "is_featured": False,
                }
            )
        return profiles

    def seed_locations(self, franchises):
        cities = [
            ("Warszawa", "Mazowieckie", Decimal("52.229700"), Decimal("21.012200")),
            ("Kraków", "Małopolskie", Decimal("50.064700"), Decimal("19.945000")),
            ("Wrocław", "Dolnośląskie", Decimal("51.107900"), Decimal("17.038500")),
            ("Poznań", "Wielkopolskie", Decimal("52.406400"), Decimal("16.925200")),
            ("Gdańsk", "Pomorskie", Decimal("54.352000"), Decimal("18.646600")),
            ("Łódź", "Łódzkie", Decimal("51.759200"), Decimal("19.456000")),
            ("Białystok", "Podlaskie", Decimal("53.132500"), Decimal("23.168800")),
            ("Lublin", "Lubelskie", Decimal("51.246500"), Decimal("22.568400")),
            ("Szczecin", "Zachodniopomorskie", Decimal("53.428500"), Decimal("14.552800")),
            ("Katowice", "Śląskie", Decimal("50.264900"), Decimal("19.023800")),
            ("Rzeszów", "Podkarpackie", Decimal("50.041300"), Decimal("21.999000")),
            ("Bydgoszcz", "Kujawsko-pomorskie", Decimal("53.123500"), Decimal("18.008400")),
            ("Olsztyn", "Warmińsko-mazurskie", Decimal("53.778400"), Decimal("20.480100")),
            ("Kielce", "Świętokrzyskie", Decimal("50.866100"), Decimal("20.628600")),
            ("Opole", "Opolskie", Decimal("50.675100"), Decimal("17.921300")),
            ("Zielona Góra", "Lubuskie", Decimal("51.935600"), Decimal("15.506200")),
            ("Kalisz", "Wielkopolskie", Decimal("51.761100"), Decimal("18.091000")),
            ("Płock", "Mazowieckie", Decimal("52.546300"), Decimal("19.706500")),
            ("Nowy Sącz", "Małopolskie", Decimal("49.617500"), Decimal("20.715300")),
            ("Kołobrzeg", "Zachodniopomorskie", Decimal("54.175900"), Decimal("15.583300")),
        ]
        for index, franchise in enumerate(franchises.values()):
            FranchiseLocation.objects.filter(
                franchise=franchise,
                address__startswith="Demo street",
            ).delete()
            FranchiseLocation.objects.filter(
                franchise=franchise,
                address__startswith="Punkt demonstracyjny na mapie",
            ).delete()
            for offset in range(10):
                city, region, lat, lng = cities[(index * 3 + offset * 2) % len(cities)]
                latitude_offset = Decimal(((index + offset) % 5) - 2) / Decimal("1000")
                longitude_offset = Decimal(((index * 2 + offset) % 5) - 2) / Decimal("1000")
                FranchiseLocation.objects.update_or_create(
                    franchise=franchise,
                    name=f"Obszar demonstracyjny - {city}",
                    defaults={
                        "location_type": FranchiseLocation.LOCATION_TYPE_AVAILABLE_AREA,
                        "city": city,
                        "region": region,
                        "address": "Punkt demonstracyjny na mapie - nie jest potwierdzoną placówką.",
                        "latitude": lat + latitude_offset,
                        "longitude": lng + longitude_offset,
                        "is_active": True,
                    },
                )

    def seed_subscriptions(self, organizations):
        plan_cycle = ["premium", "basic", "enterprise", "premium", "basic"]
        for index, organization in enumerate(organizations.values()):
            plan = Plan.objects.filter(slug=plan_cycle[index % len(plan_cycle)]).first()
            if not plan:
                continue
            OrganizationSubscription.objects.update_or_create(
                organization=organization,
                plan=plan,
                defaults={
                    "status": OrganizationSubscription.STATUS_ACTIVE,
                    "starts_at": timezone.now(),
                    "ends_at": None,
                    "manual_payment_status": OrganizationSubscription.PAYMENT_PAID,
                    "admin_notes": "Demo subscription seeded for MVP presentation.",
                },
            )

    def seed_promotions(self, franchises):
        promoted = [
            ("zabka", FranchisePromotion.TYPE_FEATURED, 100),
            ("mcdonalds", FranchisePromotion.TYPE_SEARCH_BOOST, 90),
            ("zahir-kebab", FranchisePromotion.TYPE_FEATURED, 80),
            ("xtreme-fitness-gyms", FranchisePromotion.TYPE_VERIFIED_BADGE, 70),
        ]
        for slug, promotion_type, priority in promoted:
            FranchisePromotion.objects.update_or_create(
                franchise=franchises[slug],
                promotion_type=promotion_type,
                defaults={
                    "status": FranchisePromotion.STATUS_ACTIVE,
                    "starts_at": timezone.now(),
                    "ends_at": None,
                    "priority": priority,
                    "admin_notes": "Demo active promotion.",
                },
            )

    def seed_leads(self, franchises):
        names = [
            ("Anna Nowak", "anna.nowak@example.com", "+48 600 100 200", "Warszawa", "250000"),
            ("Piotr Zieliński", "piotr.zielinski@example.com", "+48 600 200 300", "Kraków", "450000"),
            ("Marta Wiśniewska", "marta.wisniewska@example.com", "+48 600 300 400", "Gdańsk", "120000"),
            ("Tomasz Wójcik", "tomasz.wojcik@example.com", "+48 600 400 500", "Wrocław", "800000"),
            ("Katarzyna Lewandowska", "katarzyna.lewandowska@example.com", "+48 600 500 600", "Poznań", "180000"),
        ]
        statuses = [Lead.STATUS_NEW, Lead.STATUS_CONTACTED, Lead.STATUS_QUALIFIED, Lead.STATUS_SENT_TO_VENDOR]
        for franchise_index, franchise in enumerate(franchises.values()):
            for lead_index in range(2):
                name, email, phone, city, budget = names[(franchise_index + lead_index) % len(names)]
                Lead.objects.update_or_create(
                    franchise=franchise,
                    email=f"{franchise.slug}-{lead_index}-{email}",
                    defaults={
                        "name": name,
                        "phone": phone,
                        "city": city,
                        "investment_budget": Decimal(budget),
                        "message": f"Chcę poznać szczegóły współpracy z marką {franchise.name}.",
                        "status": statuses[(franchise_index + lead_index) % len(statuses)],
                        "source_path": franchise.get_absolute_url(),
                        "referrer": "https://google.com/",
                        "session_key": f"demo-session-{franchise_index}-{lead_index}",
                        "utm_source": "demo",
                        "utm_medium": "seed",
                        "utm_campaign": "mvp-demo",
                        "user_agent": "Demo browser",
                        "ip_hash": f"demo-hash-{franchise_index}-{lead_index}",
                        "privacy_consent": True,
                        "marketing_consent": lead_index % 2 == 0,
                    },
                )

    def seed_visits(self, franchises):
        for franchise_index, franchise in enumerate(franchises.values()):
            for visit_index in range(8):
                visit, _ = Visit.objects.update_or_create(
                    session_key=f"demo-visit-{franchise.slug}-{visit_index}",
                    franchise=franchise,
                    page_type=Visit.PAGE_TYPE_FRANCHISE_DETAIL,
                    defaults={
                        "path": franchise.get_absolute_url(),
                        "full_path": franchise.get_absolute_url(),
                        "referrer": "https://google.com/" if visit_index % 2 == 0 else "https://linkedin.com/",
                        "utm_source": "google" if visit_index % 2 == 0 else "linkedin",
                        "utm_medium": "organic" if visit_index % 2 == 0 else "social",
                        "utm_campaign": "demo-traffic",
                        "user_agent": "Demo browser",
                        "ip_hash": f"demo-visit-hash-{franchise_index}-{visit_index}",
                    },
                )
                VisitEvent.objects.update_or_create(
                    visit=visit,
                    event_type=VisitEvent.EVENT_PAGE_VIEW,
                    defaults={"value": franchise.name, "metadata": {"seed": True}},
                )

    def seed_content(self, categories, franchises):
        article_category, _ = ArticleCategory.objects.update_or_create(
            slug="poradnik-franczyzowy",
            defaults={
                "name": "Poradnik franczyzowy",
                "description": "Materiały edukacyjne dla osób porównujących franczyzy.",
                "sort_order": 10,
                "is_active": True,
            },
        )
        articles = [
            (
                "jak-porownywac-franczyzy",
                "Jak porównywać franczyzy przed pierwszą rozmową",
                "Sprawdź inwestycję, wymagania lokalowe, opłaty oraz dostępność wsparcia operacyjnego.",
            ),
            (
                "franczyza-gastronomiczna-czy-sklep",
                "Franczyza gastronomiczna czy sklep convenience?",
                "Porównanie dwóch popularnych segmentów: skali inwestycji, operacji i codziennego zarządzania.",
            ),
            (
                "co-to-jest-oplata-franczyzowa",
                "Co oznaczają opłaty franczyzowe i marketingowe",
                "Krótki przewodnik po najważniejszych kosztach, które warto sprawdzić przed podpisaniem umowy.",
            ),
        ]
        for index, (slug, title, excerpt) in enumerate(articles):
            Article.objects.update_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "category": article_category,
                    "excerpt": excerpt,
                    "body": f"{excerpt}\n\nTen artykuł jest przykładową treścią seedowaną dla MVP katalogu franczyz. Rozwiń go później o realną analizę, cytaty i źródła.",
                    "status": Article.STATUS_PUBLISHED,
                    "is_featured": index == 0,
                    "published_at": timezone.now(),
                    "seo_title": title,
                    "seo_description": excerpt[:300],
                },
            )

        landing_pages = [
            ("franczyzy-gastronomiczne", "Franczyzy gastronomiczne", categories["gastronomia"]),
            ("franczyzy-do-200-tys", "Franczyzy do 200 tys. zł", None),
            ("franczyzy-fitness", "Franczyzy fitness", categories["fitness"]),
        ]
        for slug, title, category in landing_pages:
            landing_page, _ = LandingPage.objects.update_or_create(
                slug=slug,
                defaults={
                    "title": title,
                    "subtitle": "Porównaj marki, inwestycję i podstawowe parametry.",
                    "intro": "Demo landing page dla SEO i filtrowania katalogu franczyz.",
                    "body": "Ta strona jest przykładową stroną SEO. W kolejnych etapach można dodać dłuższy opis, FAQ i porównania.",
                    "status": LandingPage.STATUS_PUBLISHED,
                    "is_featured": True,
                    "published_at": timezone.now(),
                    "related_category": category,
                    "cta_label": "Zobacz franczyzy",
                    "cta_url": "/franchises/",
                    "seo_title": title,
                    "seo_description": "Zobacz przykładowe marki franczyzowe i porównaj podstawowe parametry.",
                },
            )
            if category:
                landing_page.selected_franchises.set(
                    [franchise for franchise in franchises.values() if franchise.category_id == category.id][:6]
                )
            else:
                landing_page.selected_franchises.set(
                    [franchise for franchise in franchises.values() if franchise.min_investment and franchise.min_investment <= Decimal("200000")]
                )
