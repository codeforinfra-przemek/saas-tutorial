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


SOURCE_TYPE_CHOICES = (
    ("", "— wybierz —"),
    ("official", "Oficjalne źródło marki"),
    ("government", "Administracja publiczna"),
    ("regulator", "Regulator"),
    ("registry", "Rejestr"),
    ("court", "Sąd"),
    ("legal_document", "Dokument prawny / umowa"),
    ("audited_financial", "Sprawozdanie audytowane"),
    ("reputable_media", "Renomowane media"),
    ("industry", "Źródło branżowe"),
    ("marketplace", "Katalog / marketplace"),
    ("franchisee_interview", "Wywiad z franczyzobiorcą"),
    ("review_platform", "Platforma opinii"),
    ("social", "Social media"),
    ("unknown", "Nieustalony"),
)


class BenchmarkGoldFieldForm(forms.Form):
    target_field = forms.CharField(max_length=500, widget=forms.HiddenInput)
    status = forms.ChoiceField(
        choices=(
            ("pending", "Do zbadania"),
            ("found", "Znaleziono"),
            ("not_public", "Brak publiczny"),
            ("not_applicable", "Nie dotyczy"),
        )
    )
    canonical_value = forms.CharField(required=False, widget=forms.Textarea)
    source_url = forms.URLField(required=False, max_length=4000)
    source_type = forms.ChoiceField(required=False, choices=SOURCE_TYPE_CHOICES)
    observed_at = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    valid_as_of = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def clean(self):
        data = super().clean()
        if data.get("status") == "found":
            for name in ("canonical_value", "source_url", "source_type", "observed_at"):
                if not data.get(name):
                    self.add_error(name, "Wymagane, gdy oznaczasz pole jako znalezione.")
        return data


class BenchmarkGoldPromotionForm(forms.Form):
    workspace_id = forms.UUIDField(widget=forms.HiddenInput)
    gold_sha256 = forms.RegexField(
        regex=r"^[a-f0-9]{64}$",
        widget=forms.HiddenInput,
    )
    selected_field_ids = forms.MultipleChoiceField(
        choices=(),
        widget=forms.CheckboxSelectMultiple,
        label="Pola do przeniesienia",
    )

    def __init__(self, *args, promotion_rows=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selected_field_ids"].choices = [
            (
                str(row.review_field.pk),
                f"{row.policy.label} ({row.gold.target_field})",
            )
            for row in promotion_rows
            if row.selectable and row.review_field is not None
        ]

    def clean_selected_field_ids(self):
        return [int(value) for value in self.cleaned_data["selected_field_ids"]]


class BenchmarkSubmissionFieldForm(forms.Form):
    target_field = forms.CharField(max_length=500, widget=forms.HiddenInput)
    proposal_status = forms.ChoiceField(
        choices=(
            ("not_assessed", "Nie oceniono"),
            ("proposed", "Jest propozycja"),
            ("gap", "Udokumentowany brak"),
        )
    )
    proposed_value = forms.CharField(required=False, widget=forms.Textarea)
    review_decision = forms.ChoiceField(
        choices=(
            ("not_reviewed", "Nie sprawdzono"),
            ("accepted_unchanged", "Zaakceptowano bez zmian"),
            ("accepted_edited", "Zaakceptowano po korekcie"),
            ("rejected", "Odrzucono"),
            ("gap", "Potwierdzono brak"),
        )
    )
    source_url = forms.URLField(required=False, max_length=4000)
    source_type = forms.ChoiceField(required=False, choices=SOURCE_TYPE_CHOICES)
    observed_at = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    valid_as_of = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    is_demo_value = forms.BooleanField(required=False)
    demo_disclosed = forms.BooleanField(required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea)

    def clean(self):
        data = super().clean()
        status = data.get("proposal_status")
        decision = data.get("review_decision")
        if status == "proposed" and not data.get("proposed_value"):
            self.add_error("proposed_value", "Propozycja wymaga wartości.")
        if status != "proposed" and decision in {
            "accepted_unchanged", "accepted_edited", "rejected"
        }:
            self.add_error("review_decision", "Ta decyzja wymaga propozycji wartości.")
        if status == "gap" and decision not in {"gap", "not_reviewed"}:
            self.add_error("review_decision", "Brak można tylko potwierdzić albo zostawić bez oceny.")
        return data


class BenchmarkMetricsForm(forms.Form):
    tasks_attempted = forms.IntegerField(min_value=0)
    tasks_total = forms.IntegerField(min_value=1)
    research_minutes = forms.FloatField(min_value=0)
    review_minutes = forms.FloatField(min_value=0)
    known_cost_usd = forms.DecimalField(min_value=0, max_digits=12, decimal_places=6)

    def clean(self):
        data = super().clean()
        if data.get("tasks_attempted", 0) > data.get("tasks_total", 0):
            self.add_error("tasks_attempted", "Nie może przekraczać liczby wszystkich zadań.")
        return data


class BenchmarkCampaignForm(forms.Form):
    max_cost_usd = forms.DecimalField(
        min_value=0.10,
        max_value=20,
        decimal_places=2,
        initial=0.75,
        label="Maksymalny koszt jednej marki (USD)",
    )
    max_concurrent_runs = forms.IntegerField(
        min_value=1,
        max_value=2,
        initial=1,
        label="Równoległe runy",
        help_text="Jeden run naraz jest stabilniejszy i ułatwia analizę kosztów.",
    )
    acknowledge_scope = forms.BooleanField(
        label=(
            "Potwierdzam dokładną kohortę benchmarkową, w tym wewnętrzny research "
            "marek nieaktywnych bez ich publikowania."
        ),
    )
    acknowledge_paid = forms.BooleanField(
        label=(
            "Rozumiem, że utworzenie kampanii doda płatne runy do kolejki i że "
            "ostateczny koszt może dojść do pokazanego limitu."
        ),
    )

    def __init__(self, *args, brand_count=10, **kwargs):
        super().__init__(*args, **kwargs)
        self.brand_count = brand_count
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = (
                    "mt-0.5 h-4 w-4 rounded border-slate-300 text-violet-600 "
                    "focus:ring-violet-500"
                )
            else:
                field.widget.attrs["class"] = (
                    "mt-1 block w-full rounded-lg border border-slate-300 bg-white "
                    "px-3 py-2 text-sm"
                )

    @property
    def initial_total_cost(self):
        return self.fields["max_cost_usd"].initial * self.brand_count
