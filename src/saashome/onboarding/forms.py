from pathlib import Path

from django import forms

from .models import ClaimProfileRequest


FIELD_CLASSES = "block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-950 shadow-sm focus:border-brand-500 focus:ring-brand-500"


class ClaimProfileRequestForm(forms.ModelForm):
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = ClaimProfileRequest
        fields = (
            "claimant_name", "claimant_email", "claimant_phone", "claimant_role",
            "company_name", "company_website", "company_email", "message",
            "proof_url", "proof_file", "privacy_consent",
        )
        labels = {
            "claimant_name": "Imię i nazwisko", "claimant_email": "Email służbowy",
            "claimant_phone": "Telefon", "claimant_role": "Stanowisko / rola",
            "company_name": "Nazwa firmy", "company_website": "Strona firmy",
            "company_email": "Email firmy", "message": "Wiadomość", "proof_url": "Link potwierdzający związek z marką",
            "proof_file": "Dokument potwierdzający", "privacy_consent": "Wyrażam zgodę na kontakt w sprawie weryfikacji zgłoszenia.",
        }
        widgets = {"message": forms.Textarea(attrs={"rows": 4, "placeholder": "Opisz krótko swoją rolę i związek z marką."})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["privacy_consent"].required = True
        for name, field in self.fields.items():
            if name not in ("privacy_consent", "website"):
                field.widget.attrs["class"] = FIELD_CLASSES
        self.fields["privacy_consent"].widget.attrs["class"] = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Invalid submission.")
        return ""

    def clean_proof_file(self):
        proof_file = self.cleaned_data.get("proof_file")
        if not proof_file:
            return proof_file
        if proof_file.size > 10 * 1024 * 1024:
            raise forms.ValidationError("Maksymalny rozmiar pliku to 10 MB.")
        if Path(proof_file.name).suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png"}:
            raise forms.ValidationError("Dozwolone są pliki PDF, JPG i PNG.")
        return proof_file
