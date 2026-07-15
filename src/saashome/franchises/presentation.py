CATEGORY_VISUALS = {
    "convenience": {"color": "#0f9f6e", "tint": "#effdf6"},
    "gastronomia": {"color": "#e05d24", "tint": "#fff4ed"},
    "fitness": {"color": "#2563eb", "tint": "#eff6ff"},
    "edukacja": {"color": "#7c3aed", "tint": "#f5f3ff"},
    "uslugi": {"color": "#0f766e", "tint": "#f0fdfa"},
}
DEFAULT_CATEGORY_VISUAL = {"color": "#475569", "tint": "#f1f5f9"}


def category_visual(slug):
    return CATEGORY_VISUALS.get(slug, DEFAULT_CATEGORY_VISUAL)


def decorate_categories(categories):
    categories = list(categories)
    for category in categories:
        visual = category_visual(category.slug)
        category.ui_color = visual["color"]
        category.ui_tint = visual["tint"]
    return categories
