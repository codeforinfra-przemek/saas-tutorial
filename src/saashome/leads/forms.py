from django import forms

from .models import Lead


FIELD_CLASSES = (
    "block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 "
    "text-sm text-slate-950 shadow-sm focus:border-brand-500 focus:ring-brand-500"
)


class LeadForm(forms.ModelForm):
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Lead
        fields = (
            "name",
            "email",
            "phone",
            "city",
            "investment_budget",
            "message",
            "privacy_consent",
            "marketing_consent",
        )
        labels = {
            "name": "Imię i nazwisko",
            "email": "Email",
            "phone": "Telefon",
            "city": "Miasto",
            "investment_budget": "Budżet inwestycyjny",
            "message": "Wiadomość",
            "privacy_consent": "Akceptuję kontakt w sprawie tej franczyzy i przetwarzanie danych w tym celu.",
            "marketing_consent": "Chcę otrzymywać dodatkowe informacje o podobnych franczyzach.",
        }
        widgets = {
            "message": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Napisz krótko, czego chcesz się dowiedzieć.",
                }
            ),
            "investment_budget": forms.NumberInput(attrs={"min": "0", "step": "1000"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["privacy_consent"].required = True
        for name, field in self.fields.items():
            if name in ("privacy_consent", "marketing_consent", "website"):
                continue
            field.widget.attrs["class"] = FIELD_CLASSES

        self.fields["name"].widget.attrs["placeholder"] = "Jan Kowalski"
        self.fields["email"].widget.attrs["placeholder"] = "jan@example.com"
        self.fields["phone"].widget.attrs["placeholder"] = "+48 600 000 000"
        self.fields["city"].widget.attrs["placeholder"] = "Warszawa"
        self.fields["investment_budget"].widget.attrs["placeholder"] = "150000"

        checkbox_class = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
        self.fields["privacy_consent"].widget.attrs["class"] = checkbox_class
        self.fields["marketing_consent"].widget.attrs["class"] = checkbox_class

    def clean_website(self):
        value = self.cleaned_data.get("website", "")
        if value:
            raise forms.ValidationError("Invalid submission.")
        return value


class LeadManagementForm(forms.ModelForm):
    class Meta:
        model = Lead
        fields = (
            "franchise",
            "status",
            "name",
            "email",
            "phone",
            "city",
            "investment_budget",
            "message",
            "privacy_consent",
            "marketing_consent",
            "admin_notes",
            "contacted_at",
            "sent_to_vendor_at",
        )
        labels = {
            "franchise": "Franczyza",
            "status": "Status",
            "name": "Imię i nazwisko",
            "email": "Email",
            "phone": "Telefon",
            "city": "Miasto",
            "investment_budget": "Budżet inwestycyjny",
            "message": "Wiadomość klienta",
            "privacy_consent": "Zgoda na kontakt",
            "marketing_consent": "Zgoda marketingowa",
            "admin_notes": "Notatki administracyjne",
            "contacted_at": "Data kontaktu",
            "sent_to_vendor_at": "Data przekazania do vendora",
        }
        widgets = {
            "message": forms.Textarea(attrs={"rows": 4}),
            "admin_notes": forms.Textarea(attrs={"rows": 5}),
            "contacted_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "sent_to_vendor_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "investment_budget": forms.NumberInput(attrs={"min": "0", "step": "1000"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name in ("privacy_consent", "marketing_consent"):
                field.widget.attrs["class"] = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            else:
                field.widget.attrs["class"] = FIELD_CLASSES


class LeadStatusForm(forms.Form):
    VENDOR_STATUS_CHOICES = (
        (Lead.STATUS_NEW, "New"),
        (Lead.STATUS_CONTACTED, "Contacted"),
        (Lead.STATUS_QUALIFIED, "Qualified"),
        (Lead.STATUS_REJECTED, "Rejected"),
        (Lead.STATUS_CLOSED, "Closed"),
    )

    status = forms.ChoiceField(label="Status", choices=VENDOR_STATUS_CHOICES)
    rejected_reason = forms.CharField(label="Powód odrzucenia", max_length=255, required=False)
    note = forms.CharField(
        label="Notatka",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Dodaj krótką notatkę do historii leada."}),
    )

    def __init__(self, *args, **kwargs):
        current_status = kwargs.pop("current_status", None)
        super().__init__(*args, **kwargs)
        if current_status:
            self.fields["status"].initial = current_status
        for field in self.fields.values():
            field.widget.attrs["class"] = FIELD_CLASSES

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("status") == Lead.STATUS_REJECTED and not cleaned_data.get("rejected_reason"):
            self.add_error("rejected_reason", "Podaj powód odrzucenia leada.")
        return cleaned_data
