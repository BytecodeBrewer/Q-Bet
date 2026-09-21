from __future__ import annotations

from django import template

from qbet.web.display_preferences import (
    DisplayPreferences,
    currency_preference_notice,
    format_datetime,
    format_money,
)

register = template.Library()


@register.simple_tag
def localized_datetime(value, preferences: DisplayPreferences) -> str:
    return format_datetime(value, preferences)


@register.simple_tag
def localized_money(value, currency: str, preferences: DisplayPreferences) -> str:
    return format_money(value, currency, preferences)


@register.simple_tag
def currency_preference_note(source_currency: str, preferences: DisplayPreferences) -> str:
    return currency_preference_notice(source_currency, preferences)
