from datetime import UTC, datetime
from decimal import Decimal

from qbet.web.display_preferences import (
    DisplayPreferences,
    format_datetime,
    format_money,
    normalize_display_preferences,
)


def test_region_supplies_defaults_without_overwriting_explicit_choices() -> None:
    preferences = normalize_display_preferences(
        region="US",
        timezone_name="UTC",
        time_format="24h",
        currency="EUR",
    )

    assert preferences.language == "en"
    assert preferences.region == "US"
    assert preferences.timezone_name == "UTC"
    assert preferences.time_format == "24h"
    assert preferences.currency == "EUR"


def test_invalid_values_fall_back_to_safe_defaults() -> None:
    preferences = normalize_display_preferences(
        language="unsupported",
        region="unknown",
        timezone_name="Mars/Olympus",
        time_format="whenever",
        currency="BTC",
    )

    assert preferences == DisplayPreferences()


def test_datetime_and_money_formatting_only_changes_presentation() -> None:
    preferences = DisplayPreferences(
        language="de", timezone_name="Europe/Berlin", time_format="24h"
    )

    assert format_datetime(datetime(2026, 9, 21, 10, 30, tzinfo=UTC), preferences) == (
        "21.09.2026 12:30"
    )
    assert format_money(Decimal("1234.5"), "EUR", preferences) == "1.234,50 EUR"
