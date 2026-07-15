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
