from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Organization
from billing.models import FranchisePromotion, OrganizationSubscription, Plan
from content.models import Article, ArticleCategory, LandingPage
from franchises.models import Franchise, FranchiseCategory, FranchiseLocation
from leads.models import Lead
from visits.models import Visit, VisitEvent


class Command(BaseCommand):
    help = "Seed richer demo data for the franchise SaaS MVP."

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
        self.stdout.write(self.style.SUCCESS("Demo data seeded."))

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
        franchises = {}
        for item in data:
            slug = item["slug"]
            defaults = {
                "name": item["name"],
                "category": categories[item["category"]],
                "organization": organizations[item["organization"]],
                "short_description": item["short_description"],
                "description": item["description"],
                "website_url": item["website_url"],
                "min_investment": Decimal(item["min_investment"]),
                "max_investment": Decimal(item["max_investment"]),
                "initial_fee": Decimal(item["initial_fee"]),
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

    def seed_locations(self, franchises):
        cities = [
            ("Warszawa", "Mazowieckie", Decimal("52.229700"), Decimal("21.012200")),
            ("Kraków", "Małopolskie", Decimal("50.064700"), Decimal("19.945000")),
            ("Wrocław", "Dolnośląskie", Decimal("51.107900"), Decimal("17.038500")),
            ("Poznań", "Wielkopolskie", Decimal("52.406400"), Decimal("16.925200")),
            ("Gdańsk", "Pomorskie", Decimal("54.352000"), Decimal("18.646600")),
            ("Łódź", "Łódzkie", Decimal("51.759200"), Decimal("19.456000")),
        ]
        for index, franchise in enumerate(franchises.values()):
            for offset in range(2):
                city, region, lat, lng = cities[(index + offset) % len(cities)]
                FranchiseLocation.objects.update_or_create(
                    franchise=franchise,
                    name=f"{franchise.name} - {city}",
                    defaults={
                        "location_type": FranchiseLocation.LOCATION_TYPE_EXISTING_UNIT if offset == 0 else FranchiseLocation.LOCATION_TYPE_AVAILABLE_AREA,
                        "city": city,
                        "region": region,
                        "address": f"Demo street {index + offset + 1}",
                        "latitude": lat + Decimal(index) / Decimal("1000"),
                        "longitude": lng + Decimal(offset) / Decimal("1000"),
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
