"""Shared presentation context for every template using the authenticated app shell."""

from __future__ import annotations

from typing import cast

from django.contrib.auth.models import User
from django.http import HttpRequest

from qbet.web.controls import presentation_preferences
from qbet.web.display_preferences import DisplayPreferenceRepository, DisplayPreferences
from qbet.web.ui_copy import ui_copy

_DISPLAY_PREFERENCES = DisplayPreferenceRepository()


def shell_context(request: HttpRequest) -> dict[str, object]:
    cached = getattr(request, "_qbet_shell_context", None)
    if isinstance(cached, dict):
        return cached

    display_preferences = (
        _DISPLAY_PREFERENCES.load(cast(User, request.user))
        if request.user.is_authenticated
        else DisplayPreferences()
    )
    context: dict[str, object] = {
        "preferences": presentation_preferences(request.session),
        "display_preferences": display_preferences,
        "ui": ui_copy(display_preferences.language),
    }
    setattr(request, "_qbet_shell_context", context)
    return context


def authenticated_shell(request: HttpRequest) -> dict[str, object]:
    """Expose shell labels and display preferences to standard and auth templates."""

    return shell_context(request)