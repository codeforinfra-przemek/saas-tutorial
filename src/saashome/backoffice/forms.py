from django import forms

from .models import SalesActivity, SalesOpportunity


class SalesActivityForm(forms.ModelForm):
    class Meta:
        model = SalesActivity
        fields = ("activity_type", "contact", "subject", "body", "due_at", "completed_at")
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4}),
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "completed_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, opportunity=None, **kwargs):
        super().__init__(*args, **kwargs)
        if opportunity:
            self.fields["contact"].queryset = opportunity.account.contacts.all()


class SalesOpportunityStageForm(forms.ModelForm):
    class Meta:
        model = SalesOpportunity
        fields = (
            "stage", "probability", "expected_monthly_value", "expected_annual_value",
            "expected_close_date", "next_follow_up_at", "lost_reason", "churn_reason", "notes",
        )
        widgets = {
            "expected_close_date": forms.DateInput(attrs={"type": "date"}),
            "next_follow_up_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_probability(self):
        probability = self.cleaned_data["probability"]
        if not 0 <= probability <= 100:
            raise forms.ValidationError("Prawdopodobieństwo musi być w zakresie 0-100.")
        return probability

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("stage") == SalesOpportunity.STAGE_LOST and not cleaned_data.get("lost_reason"):
            self.add_error("lost_reason", "Podaj powód utraty szansy.")
        if cleaned_data.get("stage") == SalesOpportunity.STAGE_CHURNED and not cleaned_data.get("churn_reason"):
            self.add_error("churn_reason", "Podaj powód churnu.")
        return cleaned_data
