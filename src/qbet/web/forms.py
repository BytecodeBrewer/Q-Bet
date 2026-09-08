from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from qbet.simulation import SimulationEngine
from qbet.workflow.routing import EngineModes, RoutingConfiguration

_ROUTING_MODE_CHOICES = (
    ("inactive", "Inactive"),
    ("simulation", "Simulation only"),
    ("execution", "Execution only"),
    ("both", "Simulation and Execution"),
)


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


class RoutingConfigurationForm(forms.Form):
    """Staff control for the two supported v1 engine routing boundaries."""

    bonus = forms.ChoiceField(
        label="BonusEngine",
        choices=_ROUTING_MODE_CHOICES,
        widget=forms.RadioSelect,
    )
    sports_capital = forms.ChoiceField(
        label="SportsCapitalEngine",
        choices=_ROUTING_MODE_CHOICES,
        widget=forms.RadioSelect,
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        allowed = {*self.fields, "csrfmiddlewaretoken"}
        unexpected = set(self.data) - allowed
        if unexpected:
            raise forms.ValidationError("Unsupported routing configuration field.")
        return cleaned

    @staticmethod
    def initial_from_configuration(configuration: RoutingConfiguration) -> dict[str, str]:
        return {
            "bonus": _routing_choice(configuration.bonus),
            "sports_capital": _routing_choice(configuration.sports_capital),
        }

    def to_configuration(self) -> RoutingConfiguration:
        if not self.is_valid():
            raise ValueError("routing configuration form must be valid before conversion")
        return RoutingConfiguration(
            bonus=_engine_modes(str(self.cleaned_data["bonus"])),
            sports_capital=_engine_modes(str(self.cleaned_data["sports_capital"])),
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


def _engine_modes(choice: str) -> EngineModes:
    if choice == "inactive":
        return EngineModes()
    if choice == "simulation":
        return EngineModes(simulation=True)
    if choice == "execution":
        return EngineModes(execution=True)
    if choice == "both":
        return EngineModes(simulation=True, execution=True)
    raise ValueError("unsupported routing mode")


def _routing_choice(modes: EngineModes) -> str:
    if modes.simulation and modes.execution:
        return "both"
    if modes.simulation:
        return "simulation"
    if modes.execution:
        return "execution"
    return "inactive"
