from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.contrib.auth.forms import UserCreationForm
from django import forms
from django.utils.text import slugify

from .models import UserProfile


FIELD_CLASSES = (
    "block w-full rounded-lg border border-slate-200 bg-white px-4 py-3 "
    "text-sm text-slate-950 shadow-sm focus:border-brand-500 focus:ring-brand-500"
)


class SignupForm(UserCreationForm):
    class Meta:
        model = get_user_model()
        fields = ("email",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        self.fields["email"].label = "Email"
        for field in self.fields.values():
            field.widget.attrs["class"] = FIELD_CLASSES

        self.fields["email"].widget.attrs["placeholder"] = "twoj@email.pl"
        self.fields["password1"].widget.attrs["placeholder"] = "Haslo"
        self.fields["password2"].widget.attrs["placeholder"] = "Powtorz haslo"

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Konto z tym adresem email już istnieje.")
        return email

    def generate_username(self, email):
        base = slugify(email.split("@")[0]) or "user"
        username = base
        counter = 1
        User = get_user_model()
        while User.objects.filter(username=username).exists():
            counter += 1
            username = f"{base}-{counter}"
        return username

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.username = self.generate_username(user.email)
        user.is_active = False
        if commit:
            user.save()
            UserProfile.objects.get_or_create(user=user)
        return user


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Email")

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {
                "class": FIELD_CLASSES,
                "placeholder": "twoj@email.pl",
                "autocomplete": "email",
            }
        )
        self.fields["password"].widget.attrs.update(
            {
                "class": FIELD_CLASSES,
                "placeholder": "Haslo",
            }
        )


class StyledPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update(
            {
                "class": FIELD_CLASSES,
                "placeholder": "twoj@email.pl",
            }
        )


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        for field in self.fields.values():
            field.widget.attrs["class"] = FIELD_CLASSES


class ProfileForm(forms.ModelForm):
    remove_avatar = forms.BooleanField(
        required=False,
        label="Usuń obecną ikonę",
        widget=forms.CheckboxInput(
            attrs={
                "class": "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500",
            }
        ),
    )

    class Meta:
        model = UserProfile
        fields = ("avatar", "headline", "bio", "location", "website")
        widgets = {
            "avatar": forms.FileInput(
                attrs={
                    "class": (
                        "block w-full cursor-pointer rounded-lg border border-slate-200 "
                        "bg-white text-sm text-slate-700 file:mr-4 file:border-0 "
                        "file:bg-slate-950 file:px-4 file:py-3 file:text-sm "
                        "file:font-bold file:text-white hover:file:bg-slate-800"
                    ),
                    "accept": "image/*",
                }
            ),
            "bio": forms.Textarea(
                attrs={
                    "class": FIELD_CLASSES,
                    "rows": 4,
                    "placeholder": "Napisz kilka słów o sobie, planach lub typach franczyz, które Cię interesują.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name not in ("avatar", "bio", "remove_avatar"):
                field.widget.attrs["class"] = FIELD_CLASSES
