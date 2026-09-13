from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from qbet.notifications import notification_recipient_status
from qbet.simulation import SimulationEngine
from qbet.workflow.routing import EngineModes, RoutingConfiguration

_ROUTING_MODE_CHOICES = (
    ("inactive", "Inactive"),
    ("simulation", "Simulation only"),
    ("execution", "Execution only"),
    ("both", "Simulation and Execution"),
)


class NotificationReadyUserCreationForm(UserCreationForm):
    """Canonical create boundary for new users that can receive notifications."""

    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField(max_length=254)

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "password1", "password2")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        username = str(cleaned.get("username") or "")
        first_name = str(cleaned.get("first_name") or "")
        last_name = str(cleaned.get("last_name") or "")
        email = str(cleaned.get("email") or "")
        if username and first_name and last_name:
            status = notification_recipient_status(
                user_id=username,
                first_name=first_name,
                last_name=last_name,
                email=email,
            )
            if not status.ready and "email" not in self.errors:
                self.add_error("email", "Enter a valid email address for notifications.")
        return cleaned


class RegistrationForm(NotificationReadyUserCreationForm):
    """Public registration using the canonical notification-ready create boundary."""


class NotificationProfileForm(forms.ModelForm):
    """Authenticated user-maintained identity data used for notifications."""

    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField(max_length=254)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        status = notification_recipient_status(
            user_id=self.instance.get_username() or "profile-user",
            first_name=str(cleaned.get("first_name") or ""),
            last_name=str(cleaned.get("last_name") or ""),
            email=str(cleaned.get("email") or ""),
        )
        if not status.ready and "email" not in self.errors:
            self.add_error("email", "Enter a valid email address for notifications.")
        return cleaned


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
