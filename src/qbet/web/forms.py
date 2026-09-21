from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from pydantic import ValidationError as PydanticValidationError

from qbet.data.models import DataSourceMetadata, SourceTransport
from qbet.data.polling import (
    PollingCapacityClass,
    PollingStrategy,
    PollingTarget,
)
from qbet.notifications import notification_recipient_status
from qbet.notifications.preferences import NOTIFICATION_CATEGORIES
from qbet.simulation import SimulationEngine
from qbet.workflow.routing import EngineModes, RoutingConfiguration, V1Engine

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


class NotificationPreferencesForm(forms.Form):
    email_enabled = forms.BooleanField(required=False, initial=True, label="Email notifications")
    inbox_enabled = forms.BooleanField(required=False, initial=True, label="Internal inbox")
    categories = forms.MultipleChoiceField(
        required=False,
        choices=tuple(
            (category, category.replace("_", " ").title()) for category in NOTIFICATION_CATEGORIES
        ),
        widget=forms.CheckboxSelectMultiple,
    )


class PresentationSettingsForm(forms.Form):
    theme = forms.ChoiceField(
        choices=(("light", "Light"), ("dark", "Dark")),
        widget=forms.RadioSelect,
    )
    font_size = forms.ChoiceField(
        choices=(("small", "Small"), ("medium", "Medium"), ("large", "Large")),
        widget=forms.RadioSelect,
    )
    language = forms.ChoiceField(choices=(("de", "Deutsch"), ("en", "English")))
    region = forms.ChoiceField(
        choices=(("DE", "Germany"), ("GB", "United Kingdom"), ("US", "United States"))
    )
    timezone_name = forms.ChoiceField(
        label="Time zone",
        choices=(
            ("Europe/Berlin", "Europe/Berlin"),
            ("Europe/London", "Europe/London"),
            ("America/New_York", "America/New_York"),
            ("UTC", "UTC"),
        ),
    )
    time_format = forms.ChoiceField(
        label="Time format",
        choices=(("24h", "24-hour"), ("12h", "12-hour")),
    )
    currency = forms.ChoiceField(
        label="Display currency",
        choices=(("EUR", "EUR"), ("GBP", "GBP"), ("USD", "USD")),
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


class PollingStrategyForm(forms.Form):
    """Staff-only typed boundary for persisted provider-aware polling strategy."""

    provider_id = forms.CharField(max_length=255)
    source_id = forms.CharField(max_length=255)
    transport = forms.ChoiceField(
        choices=(
            (SourceTransport.API.value, "API"),
            (SourceTransport.IN_MEMORY.value, "In-memory test source"),
        )
    )
    target = forms.ChoiceField(
        choices=tuple((target.value, target.value.title()) for target in PollingTarget)
    )
    engine = forms.ChoiceField(
        required=False,
        choices=(
            ("", "Provider/target default"),
            ("bonus", "BonusEngine"),
            ("sports_capital", "SportsCapitalEngine"),
        ),
    )
    enabled = forms.BooleanField(required=False, initial=True)
    freshness_minutes = forms.IntegerField(min_value=1, initial=5)
    market_refresh_points_minutes = forms.CharField(
        required=False,
        help_text="Optional far-to-near comma-separated points, e.g. 1440,720,60.",
    )
    market_interval_minutes = forms.IntegerField(
        required=False,
        min_value=1,
        initial=5,
        help_text="Fallback cadence when no explicit market refresh points are configured.",
    )
    latest_market_poll_before_event_minutes = forms.IntegerField(min_value=1, initial=1)
    result_retry_minutes = forms.IntegerField(min_value=1, initial=10)
    max_attempts = forms.IntegerField(min_value=1, initial=3)
    capacity_class = forms.ChoiceField(
        choices=tuple((value.value, value.value.title()) for value in PollingCapacityClass),
        initial=PollingCapacityClass.FREE.value,
    )
    capacity_units = forms.IntegerField(
        required=False,
        min_value=0,
        help_text="Configured provider capacity/quota units; blank means not bounded here.",
    )
    request_cost_units = forms.IntegerField(min_value=1, initial=1)

    _strategy: PollingStrategy | None = None

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        allowed = {*self.fields, "csrfmiddlewaretoken"}
        unexpected = set(self.data) - allowed
        if unexpected:
            raise forms.ValidationError("Unsupported polling strategy field.")
        if self.errors:
            return cleaned

        points: tuple[timedelta, ...]
        raw_points = str(cleaned.get("market_refresh_points_minutes") or "").strip()
        try:
            point_minutes = (
                tuple(int(part.strip()) for part in raw_points.split(",") if part.strip())
                if raw_points
                else ()
            )
        except ValueError:
            self.add_error(
                "market_refresh_points_minutes",
                "Use comma-separated whole minutes, ordered far-to-near.",
            )
            return cleaned
        if any(value <= 0 for value in point_minutes):
            self.add_error(
                "market_refresh_points_minutes",
                "Refresh points must be positive whole minutes.",
            )
            return cleaned
        points = tuple(timedelta(minutes=value) for value in point_minutes)

        try:
            target = PollingTarget(str(cleaned["target"]))
            interval_value = cleaned.get("market_interval_minutes")
            interval = (
                timedelta(minutes=int(interval_value))
                if (interval_value is not None and target is PollingTarget.MARKET and not points)
                else None
            )
            engine = _polling_engine(cleaned.get("engine"))
            self._strategy = PollingStrategy(
                source=DataSourceMetadata(
                    provider_id=str(cleaned["provider_id"]),
                    source_id=str(cleaned["source_id"]),
                    transport=SourceTransport(str(cleaned["transport"])),
                ),
                target=target,
                engine=engine,
                enabled=bool(cleaned.get("enabled")),
                freshness_window=timedelta(minutes=int(cleaned["freshness_minutes"])),
                market_refresh_points=points,
                market_interval=interval,
                latest_market_poll_before_event=timedelta(
                    minutes=int(cleaned["latest_market_poll_before_event_minutes"])
                ),
                result_retry_interval=timedelta(minutes=int(cleaned["result_retry_minutes"])),
                max_attempts=int(cleaned["max_attempts"]),
                capacity_class=PollingCapacityClass(str(cleaned["capacity_class"])),
                capacity_units=(
                    int(cleaned["capacity_units"])
                    if cleaned.get("capacity_units") is not None
                    else None
                ),
                request_cost_units=int(cleaned["request_cost_units"]),
            )
        except (PydanticValidationError, ValueError):
            raise forms.ValidationError(
                "Polling strategy configuration is invalid. Check target-specific timing and capacity values."
            )
        return cleaned

    def to_strategy(self) -> PollingStrategy:
        if not self.is_valid() or self._strategy is None:
            raise ValueError("polling strategy form must be valid before conversion")
        return self._strategy


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


def _polling_engine(value: object) -> V1Engine | None:
    engine = str(value or "")
    if not engine:
        return None
    if engine == "bonus":
        return "bonus"
    if engine == "sports_capital":
        return "sports_capital"
    raise ValueError("unsupported polling strategy engine")


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
