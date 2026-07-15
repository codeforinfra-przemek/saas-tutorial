from django import forms

from .models import InvestorServiceRequest


FIELD_CLASSES = (
    "block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 "
    "text-sm text-slate-950 shadow-sm focus:border-brand-500 focus:ring-brand-500"
)


class InvestorServiceRequestForm(forms.ModelForm):
    class Meta:
        model = InvestorServiceRequest
        fields = (
            "service_type",
            "specialist_area",
            "name",
            "email",
            "phone",
            "city",
            "message",
            "privacy_consent",
        )
        labels = {
            "service_type": "Czego potrzebujesz?",
            "specialist_area": "Obszar wsparcia",
            "name": "Imię i nazwisko",
            "email": "Email",
            "phone": "Telefon",
            "city": "Miasto lub obszar inwestycji",
            "message": "Krótko opisz swój plan",
            "privacy_consent": "Zgadzam się na kontakt w sprawie wybranej usługi.",
        }
        widgets = {
            "message": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Np. szukam lokalu dla sklepu convenience w Warszawie."}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["privacy_consent"].required = True
        self.fields["specialist_area"].widget.attrs["placeholder"] = "Np. prawo, lokal, projekt, finansowanie"
        self.fields["name"].widget.attrs["placeholder"] = "Jan Kowalski"
        self.fields["email"].widget.attrs["placeholder"] = "jan@example.com"
        self.fields["phone"].widget.attrs["placeholder"] = "+48 600 000 000"
        self.fields["city"].widget.attrs["placeholder"] = "Warszawa"
        for name, field in self.fields.items():
            if name == "privacy_consent":
                field.widget.attrs["class"] = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            else:
                field.widget.attrs["class"] = FIELD_CLASSES

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("email") and not cleaned_data.get("phone"):
            self.add_error("email", "Podaj email albo telefon, abyśmy mogli odpowiedzieć.")
        if (
            cleaned_data.get("service_type") == InvestorServiceRequest.SERVICE_SPECIALIST_MATCH
            and not cleaned_data.get("specialist_area")
        ):
            self.add_error("specialist_area", "Wybierz lub opisz obszar wsparcia.")
        return cleaned_data
