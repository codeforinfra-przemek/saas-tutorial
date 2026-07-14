from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import get_user_model


class SignupForm(UserCreationForm):
    class Meta:
        model = get_user_model()
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field_classes = (
            "block w-full rounded-lg border border-slate-200 bg-white px-4 py-3 "
            "text-sm text-slate-950 shadow-sm focus:border-brand-500 focus:ring-brand-500"
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = field_classes

        self.fields["username"].widget.attrs["placeholder"] = "twoja-nazwa"
        self.fields["email"].widget.attrs["placeholder"] = "twoj@email.pl"
        self.fields["password1"].widget.attrs["placeholder"] = "Haslo"
        self.fields["password2"].widget.attrs["placeholder"] = "Powtorz haslo"
