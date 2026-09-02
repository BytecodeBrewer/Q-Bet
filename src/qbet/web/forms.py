from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User


class RegistrationForm(UserCreationForm):
    """Registration form that keeps account-existence errors non-enumerable."""

    class Meta:
        model = User
        fields = ("username", "password1", "password2")


class PresentationSettingsForm(forms.Form):
    theme = forms.ChoiceField(
        choices=(("light", "Light"), ("dark", "Dark")),
        widget=forms.RadioSelect,
    )
    font_size = forms.ChoiceField(
        choices=(("small", "Small"), ("medium", "Medium"), ("large", "Large")),
        widget=forms.RadioSelect,
    )
