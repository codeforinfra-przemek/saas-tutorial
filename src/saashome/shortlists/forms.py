from django import forms

from franchises.models import Franchise


FIELD_CLASSES = (
    "block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 "
    "text-sm text-slate-950 shadow-sm focus:border-brand-500 focus:ring-brand-500"
)


class MultiFranchiseLeadForm(forms.Form):
    selected_franchises = forms.ModelMultipleChoiceField(
        label="Franczyzy",
        queryset=Franchise.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
    )
    name = forms.CharField(label="Imię i nazwisko", max_length=160)
    email = forms.EmailField(label="Email")
    phone = forms.CharField(label="Telefon", max_length=40, required=False)
    city = forms.CharField(label="Miasto", max_length=120, required=False)
    investment_budget = forms.DecimalField(
        label="Budżet inwestycyjny",
        max_digits=12,
        decimal_places=2,
        min_value=0,
        required=False,
    )
    message = forms.CharField(
        label="Wiadomość",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    privacy_consent = forms.BooleanField(
        label="Akceptuję kontakt w sprawie wybranych franczyz i przetwarzanie danych w tym celu.",
        required=True,
    )
    marketing_consent = forms.BooleanField(
        label="Chcę otrzymywać dodatkowe informacje o podobnych franczyzach.",
        required=False,
    )
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if user and user.is_authenticated:
            self.fields["selected_franchises"].queryset = (
                Franchise.objects.filter(saved_by_users__user=user, is_active=True)
                .select_related("category")
                .distinct()
                .order_by("name")
            )

        for name, field in self.fields.items():
            if name in ("privacy_consent", "marketing_consent", "selected_franchises", "website"):
                continue
            field.widget.attrs["class"] = FIELD_CLASSES

        self.fields["name"].widget.attrs["placeholder"] = "Jan Kowalski"
        self.fields["email"].widget.attrs["placeholder"] = "jan@example.com"
        self.fields["phone"].widget.attrs["placeholder"] = "+48 600 000 000"
        self.fields["city"].widget.attrs["placeholder"] = "Warszawa"
        self.fields["investment_budget"].widget.attrs.update({"placeholder": "150000", "min": "0", "step": "1000"})
        self.fields["message"].widget.attrs["placeholder"] = "Napisz krótko, czego chcesz się dowiedzieć."

        checkbox_class = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
        self.fields["privacy_consent"].widget.attrs["class"] = checkbox_class
        self.fields["marketing_consent"].widget.attrs["class"] = checkbox_class

    def clean_selected_franchises(self):
        selected = self.cleaned_data["selected_franchises"]
        if selected.count() > 5:
            raise forms.ValidationError("Wybierz maksymalnie 5 franczyz.")
        return selected

    def clean_website(self):
        value = self.cleaned_data.get("website", "")
        if value:
            raise forms.ValidationError("Invalid submission.")
        return value
