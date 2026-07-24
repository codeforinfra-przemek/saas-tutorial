"""Editorial descriptions and public-profile mappings for research fields.

Every dotted field gets useful metadata. High-value fields additionally map to
the compact franchise profile; all remaining fields stay available in the full
research report.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchFieldMetadata:
    label: str
    description: str
    profile_label: str
    profile_anchor: str
    franchise_attribute: str = ""

    @property
    def appears_on_profile(self) -> bool:
        return bool(self.profile_anchor)


FIELD_LABELS = {
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
    "fees.joining_fee": "Opłata za przystąpienie",
    "fees.royalty": "Opłata bieżąca (royalty)",
    "fees.marketing": "Opłata marketingowa",
    "financing.available": "Dostępne finansowanie",
    "brand.category": "Kategoria",
    "brand.public_summary": "Krótki model oferty",
    "contact.generic_business_route": "Ogólny kontakt biznesowy",
    "offer.unit_formats": "Format współpracy",
    "candidate.capital_requirement": "Kapitał wymagany od kandydata",
    "candidate.premises_requirements": "Wymagania lokalu",
    "investment.currency": "Waluta inwestycji",
    "investment.own_contribution": "Wkład własny",
    "investment.as_of": "Data aktualności inwestycji",
    "support.training_program": "Szkolenie startowe",
    "outlets.current_total": "Placówki w Polsce",
    "outlets.as_of": "Data i zakres liczby placówek",
}

# Public L1 is a stable product contract, not merely the subset for which the
# legacy ``Franchise`` table happens to have a dedicated column.
L1_PUBLIC_FIELD_ORDER = (
    "brand.name",
    "brand.category",
    "brand.public_summary",
    "websites.official",
    "websites.franchise_offer",
    "contact.generic_business_route",
    "offer.unit_formats",
    "candidate.capital_requirement",
    "candidate.premises_requirements",
    "fees.joining_fee",
    "fees.royalty",
    "fees.marketing",
    "investment.total_low",
    "investment.total_high",
    "investment.currency",
    "investment.own_contribution",
    "investment.as_of",
    "support.training_program",
    "outlets.current_total",
    "outlets.as_of",
)

# Conservative auto-review policy. Numeric, financial, scale, category
# classification and dated commercial claims deliberately remain Human Review
# items. The catalog already has an editorial category, so a free-form model
# label must never replace it automatically.
L1_AUTO_REVIEW_SAFE_FIELDS = frozenset(
    {
        "brand.name",
        "brand.public_summary",
        "websites.official",
        "websites.franchise_offer",
        "contact.generic_business_route",
        "offer.unit_formats",
        "candidate.premises_requirements",
        "support.training_program",
    }
)
L1_AUTO_REVIEW_POLICY_VERSION = "pl-l1-safe-public-v3"

# dotted field -> (plain-language purpose, profile section, anchor, model field)
FIELD_PROFILE_MAP = {
    "brand.name": ("Nazwa, pod którą oferta jest prezentowana.", "Nagłówek profilu", "profile-overview", "name"),
    "brand.public_summary": ("Zwięzły opis publicznego modelu franczyzy.", "Nagłówek profilu", "profile-overview", "short_description"),
    "websites.official": ("Oficjalny serwis marki lub operatora.", "Nagłówek profilu", "profile-overview", "website_url"),
    "websites.franchise_offer": ("Oficjalna strona kierowana do kandydatów na franczyzobiorców.", "Nagłówek profilu", "profile-overview", "website_url"),
    "investment.total_low": ("Najniższy deklarowany łączny koszt uruchomienia placówki.", "Najważniejsze parametry inwestycji", "investor-snapshot", "min_investment"),
    "investment.total_high": ("Najwyższy deklarowany łączny koszt uruchomienia placówki.", "Najważniejsze parametry inwestycji", "investor-snapshot", "max_investment"),
    "fees.initial": ("Jednorazowa opłata wejściowa należna sieci.", "Najważniejsze parametry inwestycji", "investor-snapshot", "initial_fee"),
    "fees.joining_fee": ("Jednorazowa opłata za przystąpienie do systemu.", "Najważniejsze parametry inwestycji", "investor-snapshot", "initial_fee"),
    "fees.royalty": ("Sposób i wysokość naliczania bieżącej opłaty franczyzowej.", "Model współpracy i umowa", "model-and-agreement", "royalty_fee_text"),
    "fees.marketing": ("Obowiązkowa składka lub opłata na marketing sieci.", "Model współpracy i umowa", "model-and-agreement", "marketing_fee_text"),
    "financing.available": ("Czy sieć lub jej partnerzy deklarują wsparcie finansowania.", "Model współpracy i umowa", "model-and-agreement", "financing_available"),
    "network.total_units": ("Łączna liczba działających jednostek sieci.", "Skala i ruch sieci", "network-movement", "total_units"),
    "network.poland_units": ("Liczba działających jednostek w Polsce.", "Skala i ruch sieci", "network-movement", "poland_units"),
    "network.franchised_units": ("Liczba jednostek prowadzonych przez franczyzobiorców.", "Skala i ruch sieci", "network-movement", "franchised_units"),
    "network.company_owned_units": ("Liczba jednostek własnych operatora sieci.", "Skala i ruch sieci", "network-movement", "company_owned_units"),
    "outlets.current_total": ("Aktualna liczba placówek działających w Polsce.", "Skala i ruch sieci", "network-movement", "poland_units"),
    "candidate.premises_requirements": ("Publicznie deklarowane wymagania wobec lokalu.", "Model współpracy i umowa", "model-and-agreement", "required_premises"),
    "requirements.liquid_capital": ("Minimalny wymagany kapitał płynny kandydata.", "Najważniejsze parametry inwestycji", "investor-snapshot", "liquid_capital_required"),
    "requirements.net_worth": ("Minimalny wymagany majątek netto kandydata.", "Najważniejsze parametry inwestycji", "investor-snapshot", "net_worth_required"),
    "agreement.initial_term_years": ("Podstawowy okres obowiązywania umowy.", "Model współpracy i umowa", "model-and-agreement", "franchise_term_years"),
    "agreement.renewal_term_years": ("Okres możliwego odnowienia umowy.", "Model współpracy i umowa", "model-and-agreement", "renewal_term_years"),
    "unit.typical_size_min_sqm": ("Dolna granica typowej powierzchni placówki.", "Model współpracy i umowa", "model-and-agreement", "typical_unit_size_min_sqm"),
    "unit.typical_size_max_sqm": ("Górna granica typowej powierzchni placówki.", "Model współpracy i umowa", "model-and-agreement", "typical_unit_size_max_sqm"),
    "unit.typical_staff_count": ("Typowa liczba osób potrzebnych do prowadzenia placówki.", "Model współpracy i umowa", "model-and-agreement", "typical_staff_count"),
    # L1 values without a compact legacy column are still first-class public
    # profile data. The empty model attribute means "audited virtual field".
    "brand.category": ("Kategoria, w której użytkownik porównuje model.", "Dane L1", "research-l1", ""),
    "contact.generic_business_route": ("Ogólny, publiczny kanał kontaktu biznesowego.", "Dane L1", "research-l1", ""),
    "offer.unit_formats": ("Dostępne formaty współpracy i typy jednostek.", "Dane L1", "research-l1", ""),
    "candidate.capital_requirement": ("Kapitał wymagany od kandydata, odrębny od pełnej inwestycji.", "Dane L1", "research-l1", ""),
    "investment.currency": ("Waluta podanego zakresu inwestycji.", "Dane L1", "research-l1", ""),
    "investment.own_contribution": ("Deklarowany wkład własny kandydata.", "Dane L1", "research-l1", ""),
    "investment.as_of": ("Data aktualności danych inwestycyjnych.", "Dane L1", "research-l1", ""),
    "support.training_program": ("Publicznie deklarowany program szkolenia startowego.", "Dane L1", "research-l1", ""),
    "outlets.current_total": ("Aktualna publiczna liczba placówek w Polsce.", "Dane L1", "research-l1", "poland_units"),
    "outlets.as_of": ("Data i zakres geograficzny liczby placówek.", "Dane L1", "research-l1", ""),
}

PREFIX_DESCRIPTIONS = {
    "brand": "Informacja identyfikująca markę i sposób, w jaki występuje na rynku.",
    "franchisor": "Informacja o podmiocie prawnym oferującym lub kontrolującym franczyzę.",
    "websites": "Adres internetowy służący potwierdzeniu oficjalnego źródła.",
    "jurisdictions": "Zakres terytorialny i prawny, którego dotyczy oferta.",
    "documents": "Dokument, wersja lub data potrzebna do oceny kompletności materiałów.",
    "local_law": "Polska regulacja lub zagadnienie prawne wymagające weryfikacji.",
    "offer": "Element publicznie przedstawianej oferty współpracy.",
    "investment": "Składnik nakładów potrzebnych do rozpoczęcia działalności.",
    "fees": "Opłata należna przed uruchomieniem albo w trakcie współpracy.",
    "financing": "Warunki lub dostępność finansowania inwestycji.",
    "network": "Dane o wielkości, strukturze i zmianach liczby placówek.",
    "financials": "Dane o wynikach finansowych placówek lub operatora.",
    "performance": "Miara wyników ekonomicznych, wraz z okresem i podstawą obliczenia.",
    "agreement": "Warunek umowy franczyzowej, jej czasu trwania albo zakończenia.",
    "territory": "Zasady przydziału i ochrony obszaru działania.",
    "training": "Zakres, czas i koszt szkolenia zapewnianego przez sieć.",
    "support": "Wsparcie operacyjne oferowane przed otwarciem lub w toku działalności.",
    "operations": "Wymóg dotyczący codziennego prowadzenia placówki.",
    "unit": "Parametr typowej placówki i jej organizacji.",
    "requirements": "Warunek finansowy, osobowy albo operacyjny stawiany kandydatowi.",
    "supplier": "Zasady zakupów, dostawców i obowiązkowego zaopatrzenia.",
    "marketing": "Zasady promocji marki oraz lokalnych działań marketingowych.",
    "technology": "Wymagany system, sprzęt, licencja lub koszt technologiczny.",
    "insurance": "Wymagane ubezpieczenie lub minimalny zakres ochrony.",
    "litigation": "Spór, postępowanie albo ryzyko prawne istotne dla oceny sieci.",
    "bankruptcy": "Informacja o niewypłacalności lub postępowaniu upadłościowym.",
    "ownership": "Struktura właścicielska, osoby zarządzające lub powiązania kapitałowe.",
    "privacy": "Sposób przetwarzania danych osobowych i podział odpowiedzialności.",
}


PROFILE_INFO = {
    "PL:L1": ("Poziom 1 — katalogowy", "Podstawowe dane dostępne dla przeciętnego użytkownika w popularnych źródłach."),
    "PL:L2": ("Poziom 2 — rozszerzony publiczny", "Dane z wielu stron, rejestrów i źródeł branżowych, z kontrolą dowodów."),
    "PL:L3": ("Poziom 3 — due diligence", "Publiczne źródła oraz dane wymagające pracy researchera lub dokumentów prywatnych."),
}


def humanize_field(field_name: str) -> str:
    if field_name in FIELD_LABELS:
        return FIELD_LABELS[field_name]
    leaf = field_name.rsplit(".", 1)[-1].replace("_", " ").replace("-", " ").strip()
    return leaf[:1].upper() + leaf[1:] if leaf else field_name


def field_metadata(field_name: str, *, task_title: str = "") -> ResearchFieldMetadata:
    label = humanize_field(field_name)
    mapped = FIELD_PROFILE_MAP.get(field_name)
    if mapped:
        description, profile_label, anchor, attribute = mapped
        return ResearchFieldMetadata(label, description, profile_label, anchor, attribute)
    prefix = field_name.split(".", 1)[0]
    base = PREFIX_DESCRIPTIONS.get(
        prefix,
        "Informacja zbierana w ramach szczegółowego badania franczyzy.",
    )
    if task_title:
        base = f"{base} Zakres: {task_title}."
    return ResearchFieldMetadata(
        label,
        base,
        "Pełny raport researchu",
        "",
    )


def profile_info(profile_id: str, depth: str = "") -> dict:
    lookup_id = profile_id
    if profile_id.startswith("PL:L") and ":v" in profile_id:
        lookup_id = profile_id.rsplit(":v", 1)[0]
    title, description = PROFILE_INFO.get(
        lookup_id,
        (profile_id or depth or "Profil historyczny", "Zakres zapisany w planie researchu."),
    )
    return {"id": profile_id or depth, "title": title, "description": description}
