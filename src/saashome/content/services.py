from django.db.models import Q

from franchises.models import Franchise


def get_franchises_for_landing_page(landing_page):
    franchises = Franchise.objects.filter(is_active=True).select_related("category")

    if landing_page.related_category_id:
        franchises = franchises.filter(category=landing_page.related_category)

    if landing_page.max_investment is not None and hasattr(Franchise, "min_investment"):
        franchises = franchises.filter(
            Q(min_investment__lte=landing_page.max_investment)
            | Q(min_investment__isnull=True)
        )

    if landing_page.min_investment is not None and hasattr(Franchise, "max_investment"):
        franchises = franchises.filter(
            Q(max_investment__gte=landing_page.min_investment)
            | Q(max_investment__isnull=True)
        )

    for field_name in (
        "business_type",
        "home_based",
        "part_time_possible",
        "training_provided",
        "financing_available",
    ):
        value = getattr(landing_page, field_name, None)
        if value not in (None, "") and hasattr(Franchise, field_name):
            franchises = franchises.filter(**{field_name: value})

    selected = landing_page.selected_franchises.filter(is_active=True)
    if selected.exists():
        franchises = franchises | selected

    ordering = []
    for field_name in ("-is_promoted", "-rank_score", "name"):
        model_field = field_name.lstrip("-")
        if hasattr(Franchise, model_field):
            ordering.append(field_name)
    if ordering:
        franchises = franchises.order_by(*ordering)

    return franchises.distinct()
