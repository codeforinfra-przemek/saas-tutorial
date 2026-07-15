from datetime import date

from .models import Franchise


MCDONALDS_REFERENCE_SOURCE_URL = "https://mcdonalds.pl/o-mcdonalds/wymagania-finansowe/"


def mcdonalds_reference_fields():
    """Public McDonald's Poland franchise data checked on 2026-01-27."""
    return {
        "short_description": "Sieć restauracji quick service; McDonald's Polska podaje 600 restauracji w kraju.",
        "description": (
            "McDonald's działa w Polsce od 1992 roku. Według informacji McDonald's Polska z 27 stycznia "
            "2026 roku sieć obejmuje 600 restauracji, z których ponad 90% prowadzą niezależni "
            "franczyzobiorcy. Franczyzobiorca osobiście prowadzi restaurację; lokalizację i budowę "
            "restauracji realizuje McDonald's."
        ),
        "website_url": "https://mcdonalds.pl/o-mcdonalds/franczyza/",
        # The official source gives an average equipment cost above PLN 5m, not a minimum investment.
        "min_investment": None,
        "max_investment": None,
        # The official licence fee is stated in USD, so it is not stored in a PLN-only field.
        "initial_fee": None,
        "royalty_fee_text": "Opłata podstawowa i procentowa (rent) oraz opłata licencyjna; kwoty nie są publiczne.",
        "marketing_fee_text": "Opłata marketingowa; kwota nie jest publicznie podana.",
        "business_type": Franchise.BUSINESS_TYPE_STATIONARY,
        "required_premises": "Lokalizację i budowę restauracji zapewnia McDonald's.",
        "home_based": False,
        "part_time_possible": False,
        "training_provided": True,
        "financing_available": False,
        "founded_year": 1955,
        "franchising_since": 1955,
        "total_units": None,
        "poland_units": 600,
        "franchised_units": None,
        "company_owned_units": None,
        "units_opened_last_year": None,
        "units_closed_last_year": None,
        "units_transferred_last_year": None,
        "unit_growth_percent_1y": None,
        "liquid_capital_required": 2000000,
        "net_worth_required": None,
        "franchise_term_years": 20,
        "renewal_term_years": None,
        "estimated_payback_months": None,
        "mature_unit_revenue_annual": None,
        "mature_unit_operating_profit_annual": None,
        "mature_unit_count": None,
        "typical_unit_size_min_sqm": None,
        "typical_unit_size_max_sqm": None,
        "typical_staff_count": None,
        "territory_type": "",
        "financial_performance_disclosed": False,
        "financial_performance_note": (
            "McDonald's Polska podaje średni koszt zakupu wyposażenia restauracji powyżej 5 mln zł, "
            "minimalne środki własne 2 mln zł oraz możliwość finansowania do 75% kredytem bankowym. "
            "Nie publikuje jednak porównywalnych wyników finansowych pojedynczej dojrzałej restauracji."
        ),
        "financial_data_as_of": date(2026, 1, 27),
        "data_status": Franchise.DATA_STATUS_EDITOR_VERIFIED,
        "data_source_url": MCDONALDS_REFERENCE_SOURCE_URL,
        "is_verified": True,
    }
