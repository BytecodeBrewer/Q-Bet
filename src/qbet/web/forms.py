from __future__ import annotations

from decimal import Decimal

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from qbet.simulation import SimulationEngine


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


class SimulationAvailabilityForm(forms.Form):
    enabled = forms.BooleanField(required=False, label="Simulation enabled")


class SimulationStartForm(forms.Form):
    engine = forms.ChoiceField(
        choices=(
            (SimulationEngine.BONUS.value, "BonusEngine"),
            (SimulationEngine.SPORTS_CAPITAL.value, "SportsCapitalEngine"),
        )
    )
    starting_capital = forms.DecimalField(
        min_value=Decimal("10"),
        max_value=Decimal("100000000"),
        max_digits=18,
        decimal_places=2,
        initial=Decimal("100"),
    )
    max_duration_minutes = forms.IntegerField(
        min_value=1,
        max_value=48 * 60,
        initial=60,
        help_text="Bounded simulated duration; never more than 48 hours.",
    )
