from pathlib import Path

from django import forms
from django.core.validators import MaxLengthValidator

from .models import (
    Franchise,
    FranchiseAsset,
    FranchiseCategory,
    FranchiseLocation,
    FranchiseResearchDocument,
    FranchiseResearchReviewField,
    FranchiseUpdateRequest,
)


FIELD_CLASSES = (
    "block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 "
    "text-sm text-slate-950 shadow-sm focus:border-brand-500 focus:ring-brand-500"
)


class FranchiseManagementForm(forms.ModelForm):
    class Meta:
        model = Franchise
        fields = (
            "name",
            "slug",
            "category",
            "organization",
            "short_description",
            "description",
            "logo",
            "website_url",
            "min_investment",
            "max_investment",
            "initial_fee",
            "royalty_fee_text",
            "marketing_fee_text",
            "business_type",
            "required_premises",
            "home_based",
            "part_time_possible",
            "training_provided",
            "financing_available",
            "founded_year",
            "franchising_since",
            "total_units",
            "poland_units",
            "franchised_units",
            "company_owned_units",
            "units_opened_last_year",
            "units_closed_last_year",
            "units_transferred_last_year",
            "unit_growth_percent_1y",
            "liquid_capital_required",
            "net_worth_required",
            "franchise_term_years",
            "renewal_term_years",
            "estimated_payback_months",
            "mature_unit_revenue_annual",
            "mature_unit_operating_profit_annual",
            "mature_unit_count",
            "typical_unit_size_min_sqm",
            "typical_unit_size_max_sqm",
            "typical_staff_count",
            "territory_type",
            "financial_performance_disclosed",
            "financial_performance_note",
            "financial_data_as_of",
            "data_status",
            "data_source_url",
            "rank_score",
            "popularity_score",
            "editor_rating",
            "is_verified",
            "is_promoted",
            "is_featured",
            "is_active",
        )
        labels = {
            "name": "Nazwa",
            "slug": "Slug",
            "category": "Kategoria",
            "organization": "Organizacja/vendor",
            "short_description": "Krótki opis",
            "description": "Opis szczegółowy",
            "logo": "Logo",
            "website_url": "Strona WWW",
            "min_investment": "Minimalna inwestycja",
            "max_investment": "Maksymalna inwestycja",
            "initial_fee": "Opłata wstępna",
            "royalty_fee_text": "Royalty fee",
            "marketing_fee_text": "Marketing fee",
            "business_type": "Typ biznesu",
            "required_premises": "Wymagany lokal",
            "home_based": "Możliwa praca z domu",
            "part_time_possible": "Możliwe part-time",
            "training_provided": "Szkolenie zapewnione",
            "financing_available": "Finansowanie dostępne",
            "founded_year": "Rok założenia",
            "franchising_since": "Franczyza od",
            "total_units": "Placówki globalnie",
            "poland_units": "Placówki w Polsce",
            "franchised_units": "Placówki franczyzowe",
            "company_owned_units": "Placówki własne sieci",
            "units_opened_last_year": "Otwarcia w ostatnich 12 miesiącach",
            "units_closed_last_year": "Zamknięcia w ostatnich 12 miesiącach",
            "units_transferred_last_year": "Transfery placówek w ostatnich 12 miesiącach",
            "unit_growth_percent_1y": "Wzrost sieci rok do roku (%)",
            "liquid_capital_required": "Wymagany kapitał płynny",
            "net_worth_required": "Wymagany majątek netto",
            "franchise_term_years": "Podstawowy okres umowy (lata)",
            "renewal_term_years": "Okres odnowienia umowy (lata)",
            "estimated_payback_months": "Szacowany zwrot inwestycji (miesiące)",
            "mature_unit_revenue_annual": "Średni roczny przychód dojrzałej placówki",
            "mature_unit_operating_profit_annual": "Średni roczny zysk operacyjny dojrzałej placówki",
            "mature_unit_count": "Liczba placówek w próbie finansowej",
            "typical_unit_size_min_sqm": "Typowy lokal od (m²)",
            "typical_unit_size_max_sqm": "Typowy lokal do (m²)",
            "typical_staff_count": "Typowa liczba pracowników",
            "territory_type": "Terytorium",
            "financial_performance_disclosed": "Dostępne ujawnienie wyników finansowych",
            "financial_performance_note": "Notatka o danych finansowych",
            "financial_data_as_of": "Dane finansowe na dzień",
            "data_status": "Status danych",
            "data_source_url": "Źródło danych (URL)",
            "rank_score": "Wynik rankingu",
            "popularity_score": "Popularność",
            "editor_rating": "Ocena redakcji",
            "is_verified": "Zweryfikowana",
            "is_promoted": "Promowana",
            "is_featured": "Wyróżniona",
            "is_active": "Aktywna",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 8}),
            "short_description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["category"].queryset = FranchiseCategory.objects.order_by("sort_order", "name")

        for name, field in self.fields.items():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            elif isinstance(field.widget, forms.FileInput):
                field.widget.attrs["class"] = (
                    "block w-full rounded-lg border border-slate-200 bg-white text-sm text-slate-950 "
                    "file:mr-4 file:border-0 file:bg-slate-100 file:px-4 file:py-2 file:text-sm file:font-bold file:text-slate-700"
                )
            else:
                field.widget.attrs["class"] = FIELD_CLASSES


class FranchiseLocationForm(forms.ModelForm):
    class Meta:
        model = FranchiseLocation
        fields = (
            "location_type",
            "name",
            "city",
            "region",
            "address",
            "latitude",
            "longitude",
            "is_active",
        )
        labels = {
            "location_type": "Typ lokalizacji",
            "name": "Nazwa lokalizacji",
            "city": "Miasto",
            "region": "Region",
            "address": "Adres",
            "latitude": "Szerokość geograficzna",
            "longitude": "Długość geograficzna",
            "is_active": "Aktywna",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            else:
                field.widget.attrs["class"] = FIELD_CLASSES


class FranchiseUpdateRequestForm(forms.ModelForm):
    class Meta:
        model = FranchiseUpdateRequest
        fields = (
            "short_description",
            "description",
            "website_url",
            "min_investment",
            "max_investment",
            "initial_fee",
            "royalty_fee_text",
            "marketing_fee_text",
            "business_type",
            "required_premises",
            "home_based",
            "part_time_possible",
            "training_provided",
            "financing_available",
            "founded_year",
            "franchising_since",
            "total_units",
            "poland_units",
            "franchised_units",
            "company_owned_units",
            "units_opened_last_year",
            "units_closed_last_year",
            "units_transferred_last_year",
            "unit_growth_percent_1y",
            "liquid_capital_required",
            "net_worth_required",
            "franchise_term_years",
            "renewal_term_years",
            "estimated_payback_months",
            "mature_unit_revenue_annual",
            "mature_unit_operating_profit_annual",
            "mature_unit_count",
            "typical_unit_size_min_sqm",
            "typical_unit_size_max_sqm",
            "typical_staff_count",
            "territory_type",
            "financial_performance_disclosed",
            "financial_performance_note",
            "financial_data_as_of",
            "data_status",
            "data_source_url",
        )
        labels = {
            "short_description": "Krótki opis",
            "description": "Opis szczegółowy",
            "website_url": "Strona WWW",
            "min_investment": "Minimalna inwestycja",
            "max_investment": "Maksymalna inwestycja",
            "initial_fee": "Opłata wstępna",
            "royalty_fee_text": "Royalty fee",
            "marketing_fee_text": "Marketing fee",
            "business_type": "Typ biznesu",
            "required_premises": "Wymagany lokal",
            "home_based": "Możliwa praca z domu",
            "part_time_possible": "Możliwe part-time",
            "training_provided": "Szkolenie zapewnione",
            "financing_available": "Finansowanie dostępne",
            "founded_year": "Rok założenia",
            "franchising_since": "Franczyza od",
            "total_units": "Placówki globalnie",
            "poland_units": "Placówki w Polsce",
            "franchised_units": "Placówki franczyzowe",
            "company_owned_units": "Placówki własne sieci",
            "units_opened_last_year": "Otwarcia w ostatnich 12 miesiącach",
            "units_closed_last_year": "Zamknięcia w ostatnich 12 miesiącach",
            "units_transferred_last_year": "Transfery placówek w ostatnich 12 miesiącach",
            "unit_growth_percent_1y": "Wzrost sieci rok do roku (%)",
            "liquid_capital_required": "Wymagany kapitał płynny",
            "net_worth_required": "Wymagany majątek netto",
            "franchise_term_years": "Podstawowy okres umowy (lata)",
            "renewal_term_years": "Okres odnowienia umowy (lata)",
            "estimated_payback_months": "Szacowany zwrot inwestycji (miesiące)",
            "mature_unit_revenue_annual": "Średni roczny przychód dojrzałej placówki",
            "mature_unit_operating_profit_annual": "Średni roczny zysk operacyjny dojrzałej placówki",
            "mature_unit_count": "Liczba placówek w próbie finansowej",
            "typical_unit_size_min_sqm": "Typowy lokal od (m²)",
            "typical_unit_size_max_sqm": "Typowy lokal do (m²)",
            "typical_staff_count": "Typowa liczba pracowników",
            "territory_type": "Terytorium",
            "financial_performance_disclosed": "Dostępne ujawnienie wyników finansowych",
            "financial_performance_note": "Notatka o danych finansowych",
            "financial_data_as_of": "Dane finansowe na dzień",
            "data_status": "Status danych",
            "data_source_url": "Źródło danych (URL)",
        }
        widgets = {
            "short_description": forms.Textarea(attrs={"rows": 3}),
            "description": forms.Textarea(attrs={"rows": 8}),
        }

    def __init__(self, *args, **kwargs):
        disabled = kwargs.pop("disabled", False)
        plan = kwargs.pop("plan", None)
        super().__init__(*args, **kwargs)
        max_description_length = getattr(plan, "max_description_length", 1200) or 1200
        self.fields["description"].validators.append(MaxLengthValidator(max_description_length))
        self.fields["description"].widget.attrs["maxlength"] = max_description_length
        self.fields["description"].help_text = f"Limit w obecnym planie: {max_description_length} znaków."
        for field in self.fields.values():
            field.disabled = disabled
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            else:
                field.widget.attrs["class"] = FIELD_CLASSES

    def clean(self):
        cleaned_data = super().clean()
        min_investment = cleaned_data.get("min_investment")
        max_investment = cleaned_data.get("max_investment")
        founded_year = cleaned_data.get("founded_year")
        franchising_since = cleaned_data.get("franchising_since")

        if min_investment is not None and max_investment is not None and min_investment > max_investment:
            self.add_error("max_investment", "Maksymalna inwestycja nie może być mniejsza niż minimalna.")

        if founded_year and franchising_since and franchising_since < founded_year:
            self.add_error("franchising_since", "Rok rozpoczęcia franczyzy nie może być wcześniejszy niż rok założenia.")

        return cleaned_data


class FranchiseAssetForm(forms.ModelForm):
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
    DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx"}

    class Meta:
        model = FranchiseAsset
        fields = ("title", "description", "file", "sort_order")
        labels = {
            "title": "Tytuł",
            "description": "Opis lub podpis",
            "file": "Plik",
            "sort_order": "Kolejność",
        }

    def __init__(self, *args, asset_type, **kwargs):
        self.asset_type = asset_type
        super().__init__(*args, **kwargs)
        self.fields["file"].widget.attrs["accept"] = (
            "image/jpeg,image/png,image/webp"
            if asset_type == FranchiseAsset.TYPE_IMAGE
            else ".pdf,.doc,.docx,.xls,.xlsx"
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = FIELD_CLASSES

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        extension = Path(uploaded_file.name).suffix.lower()
        allowed_extensions = (
            self.IMAGE_EXTENSIONS
            if self.asset_type == FranchiseAsset.TYPE_IMAGE
            else self.DOCUMENT_EXTENSIONS
        )
        if extension not in allowed_extensions:
            raise forms.ValidationError("Ten format pliku nie jest obsługiwany.")
        max_size = 10 * 1024 * 1024 if self.asset_type == FranchiseAsset.TYPE_IMAGE else 20 * 1024 * 1024
        if uploaded_file.size > max_size:
            raise forms.ValidationError("Plik jest za duży.")
        return uploaded_file


class ResearchReviewFieldForm(forms.ModelForm):
    class Meta:
        model = FranchiseResearchReviewField
        fields = ("reviewer_value", "reviewer_note")
        labels = {
            "reviewer_value": "Wartość po korekcie",
            "reviewer_note": "Notatka redakcyjna",
        }
        widgets = {
            "reviewer_value": forms.Textarea(attrs={"rows": 3}),
            "reviewer_note": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reviewer_value"].widget.attrs.update(
            {
                "class": FIELD_CLASSES,
                "placeholder": "Wpisz poprawną wartość lub uzupełnij brak…",
            }
        )
        self.fields["reviewer_note"].widget.attrs.update(
            {
                "class": FIELD_CLASSES,
                "placeholder": "Opcjonalnie: skąd pochodzi korekta lub co należy sprawdzić",
            }
        )


class ResearchDocumentUploadForm(forms.ModelForm):
    ALLOWED_EXTENSIONS = {
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".csv",
        ".txt",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }
    MAX_SIZE = 30 * 1024 * 1024

    class Meta:
        model = FranchiseResearchDocument
        fields = ("file", "document_type", "access_level", "notes")
        labels = {
            "file": "Plik",
            "document_type": "Rodzaj dokumentu",
            "access_level": "Poziom dostępu",
            "notes": "Opis dla zespołu",
        }
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].widget.attrs.update(
            {
                "class": (
                    "block w-full cursor-pointer rounded-lg border border-dashed "
                    "border-slate-300 bg-slate-50 p-3 text-sm"
                ),
                "accept": ".pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.jpg,.jpeg,.png,.webp",
            }
        )
        for name in ("document_type", "access_level", "notes"):
            self.fields[name].widget.attrs["class"] = FIELD_CLASSES

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]
        extension = Path(uploaded_file.name).suffix.lower()
        if extension not in self.ALLOWED_EXTENSIONS:
            raise forms.ValidationError("Ten format pliku nie jest obsługiwany.")
        if uploaded_file.size > self.MAX_SIZE:
            raise forms.ValidationError("Plik jest większy niż 30 MB.")
        return uploaded_file


class ResearchWorkspaceDecisionForm(forms.Form):
    reviewer_notes = forms.CharField(
        required=False,
        label="Podsumowanie decyzji",
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": FIELD_CLASSES,
                "placeholder": "Najważniejsze braki, zastrzeżenia i zalecenia…",
            }
        ),
    )
    acknowledge_gaps = forms.BooleanField(
        required=False,
        label="Rozumiem, że nieuzupełnione pola pozostaną udokumentowanymi brakami.",
    )
