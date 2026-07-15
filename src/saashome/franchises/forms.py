from django import forms

from .models import Franchise, FranchiseCategory, FranchiseLocation, FranchiseUpdateRequest


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
        }
        widgets = {
            "short_description": forms.Textarea(attrs={"rows": 3}),
            "description": forms.Textarea(attrs={"rows": 8}),
        }

    def __init__(self, *args, **kwargs):
        disabled = kwargs.pop("disabled", False)
        super().__init__(*args, **kwargs)
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
