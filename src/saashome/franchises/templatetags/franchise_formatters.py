from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template


register = template.Library()


@register.filter
def grouped_number(value, decimal_places=0):
    """Render a numeric value with spaces between thousands for Polish UI."""
    try:
        number = Decimal(str(value))
        places = max(int(decimal_places), 0)
    except (InvalidOperation, TypeError, ValueError):
        return value

    if places:
        rendered = f"{number:,.{places}f}"
    else:
        rounded = number.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        rendered = f"{rounded:,.0f}"

    return rendered.replace(",", " ").replace(".", ",")


RESEARCH_FIELD_LABELS = {
    "brand.name": "Nazwa marki",
    "brand.aliases": "Inne używane nazwy",
    "franchisor.legal_name": "Pełna nazwa franczyzodawcy",
    "franchisor.registration_id": "Numer rejestrowy franczyzodawcy",
    "franchisor.parent_entities": "Podmioty dominujące",
    "websites.official": "Oficjalna strona internetowa",
    "websites.franchise_offer": "Oficjalna oferta franczyzowa",
    "jurisdictions.target_country": "Kraj docelowy",
    "jurisdictions.target_regions": "Regiony docelowe",
    "investment.total_low": "Minimalna inwestycja łącznie",
    "investment.total_high": "Maksymalna inwestycja łącznie",
    "fees.initial": "Opłata wstępna",
    "fees.royalty": "Opłata bieżąca (royalty)",
    "fees.marketing": "Opłata marketingowa",
    "financing.available": "Dostępne finansowanie",
}


@register.filter
def research_field_label(value):
    """Turn pipeline field paths into approachable editorial labels."""

    value = str(value or "")
    if value in RESEARCH_FIELD_LABELS:
        return RESEARCH_FIELD_LABELS[value]
    leaf = value.rsplit(".", 1)[-1].replace("_", " ").strip()
    return leaf[:1].upper() + leaf[1:] if leaf else value
