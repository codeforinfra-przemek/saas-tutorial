"""Small, curated landing-page definitions - deliberately not a page generator."""

BUDGET_PAGES = (
    {"slug": "do-50000-zl", "title": "Franczyzy do 50 000 zl", "description": "Oferty franczyzowe z deklarowanym minimalnym nakladem do 50 000 zl.", "max_investment": 50000},
    {"slug": "do-100000-zl", "title": "Franczyzy do 100 000 zl", "description": "Porownaj franczyzy, ktore mozna rozpoczac przy budzecie do 100 000 zl.", "max_investment": 100000},
    {"slug": "do-250000-zl", "title": "Franczyzy do 250 000 zl", "description": "Zestawienie aktywnych ofert z minimalna inwestycja do 250 000 zl.", "max_investment": 250000},
    {"slug": "do-500000-zl", "title": "Franczyzy do 500 000 zl", "description": "Przegladaj franczyzy wymagajace minimalnego nakladu do 500 000 zl.", "max_investment": 500000},
)

BUSINESS_MODEL_PAGES = (
    {"slug": "bez-lokalu", "title": "Franczyzy bez lokalu", "description": "Modele franczyzowe, ktore nie wymagaja stalego lokalu na start.", "filters": {"home_based": True}},
    {"slug": "online", "title": "Franczyzy online", "description": "Oferty franczyzowe o modelu online.", "filters": {"business_type": "online"}},
    {"slug": "mobilna", "title": "Franczyzy mobilne", "description": "Oferty o mobilnym modelu dzialania.", "filters": {"business_type": "mobile"}},
    {"slug": "stacjonarna", "title": "Franczyzy stacjonarne", "description": "Profile franczyz wymagajace dzialalnosci stacjonarnej.", "filters": {"business_type": "stationary"}},
    {"slug": "home-based", "title": "Franczyzy prowadzone z domu", "description": "Franczyzy, ktore mozna rozwijac z domu lub bez stalego biura.", "filters": {"home_based": True}},
)


def get_seo_page(pages, slug):
    return next((page for page in pages if page["slug"] == slug), None)
