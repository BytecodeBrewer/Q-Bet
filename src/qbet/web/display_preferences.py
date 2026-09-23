"""Durable user-controlled display preferences and safe presentation formatting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth.models import User
from django.db import DatabaseError

from qbet.web.models import UserDisplayPreference

_LANGUAGE_VALUES: Final = ("en", "de")
_REGION_VALUES: Final = ("DE", "GB", "US")
_TIMEZONE_VALUES: Final = ("Europe/Berlin", "Europe/London", "America/New_York", "UTC")
_TIME_FORMAT_VALUES: Final = ("24h", "12h")
_CURRENCY_VALUES: Final = ("EUR", "GBP", "USD")

_REGION_DEFAULTS: Final = {
    "DE": ("de", "Europe/Berlin", "24h", "EUR"),
    "GB": ("en", "Europe/London", "24h", "GBP"),
    "US": ("en", "America/New_York", "12h", "USD"),
}
_DEFAULT_REGION: Final = "DE"


@dataclass(frozen=True)
class DisplayPreferences:
    language: str = "de"
    region: str = _DEFAULT_REGION
    timezone_name: str = "Europe/Berlin"
    time_format: str = "24h"
    currency: str = "EUR"


def normalize_display_preferences(
    *,
    language: object = None,
    region: object = None,
    timezone_name: object = None,
    time_format: object = None,
    currency: object = None,
) -> DisplayPreferences:
    """Use regional defaults only for absent or invalid values, never for valid choices."""

    selected_region = str(region) if str(region) in _REGION_VALUES else _DEFAULT_REGION
    default_language, default_timezone, default_time_format, default_currency = _REGION_DEFAULTS[
        selected_region
    ]
    return DisplayPreferences(
        language=str(language) if str(language) in _LANGUAGE_VALUES else default_language,
        region=selected_region,
        timezone_name=(
            str(timezone_name) if str(timezone_name) in _TIMEZONE_VALUES else default_timezone
        ),
        time_format=(
            str(time_format) if str(time_format) in _TIME_FORMAT_VALUES else default_time_format
        ),
        currency=str(currency) if str(currency) in _CURRENCY_VALUES else default_currency,
    )


class DisplayPreferenceRepository:
    """One durable display-only preference record per authenticated user."""

    def load(self, user: User) -> DisplayPreferences:
        try:
            row = UserDisplayPreference.objects.filter(user=user).first()
        except DatabaseError:
            return DisplayPreferences()
        if row is None:
            return DisplayPreferences()
        return normalize_display_preferences(
            language=row.language,
            region=row.region,
            timezone_name=row.timezone_name,
            time_format=row.time_format,
            currency=row.currency,
        )

    def save(self, user: User, preferences: DisplayPreferences) -> DisplayPreferences:
        normalized = normalize_display_preferences(
            language=preferences.language,
            region=preferences.region,
            timezone_name=preferences.timezone_name,
            time_format=preferences.time_format,
            currency=preferences.currency,
        )
        try:
            UserDisplayPreference.objects.update_or_create(
                user=user,
                defaults={
                    "language": normalized.language,
                    "region": normalized.region,
                    "timezone_name": normalized.timezone_name,
                    "time_format": normalized.time_format,
                    "currency": normalized.currency,
                },
            )
        except DatabaseError as error:
            raise RuntimeError("display_preferences_unavailable") from error
        return normalized


def format_datetime(value: datetime, preferences: DisplayPreferences) -> str:
    """Render one aware instant using only the selected display settings."""

    try:
        localized = value.astimezone(ZoneInfo(preferences.timezone_name))
    except (ValueError, ZoneInfoNotFoundError):
        localized = value.astimezone(ZoneInfo(DisplayPreferences().timezone_name))
    pattern = "%d.%m.%Y %H:%M" if preferences.time_format == "24h" else "%Y-%m-%d %I:%M %p"
    return localized.strftime(pattern)


def format_money(
    value: Decimal,
    source_currency: str,
    preferences: DisplayPreferences,
) -> str:
    """Format existing money without converting or changing its authoritative currency."""

    try:
        amount = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return f"{value} {source_currency}"

    separator = "," if preferences.language == "de" else "."
    grouping = "." if preferences.language == "de" else ","
    rendered = f"{amount:,.2f}".replace(",", "_").replace(".", separator).replace("_", grouping)
    return f"{rendered} {source_currency}"


def currency_preference_notice(source_currency: str, preferences: DisplayPreferences) -> str:
    """Explain the stored report-currency preference without mislabeling source amounts."""

    if source_currency == preferences.currency:
        return f"Preferred recorded currency {preferences.currency} matches this amount."
    return (
        f"Preferred recorded currency: {preferences.currency}. "
        f"This amount remains recorded and displayed in {source_currency}; no FX conversion is applied."
    )
