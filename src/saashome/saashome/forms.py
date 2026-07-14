from django import forms


class ContactRequestForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={
                "class": (
                    "block w-full rounded-lg border border-slate-200 bg-white px-4 py-3 "
                    "text-sm text-slate-950 shadow-sm focus:border-brand-500 "
                    "focus:ring-brand-500"
                ),
                "placeholder": "twoj@email.pl",
                "autocomplete": "email",
            }
        ),
    )
