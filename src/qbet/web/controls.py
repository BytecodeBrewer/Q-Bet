"""Session-scoped presentation settings that never control workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from django.contrib.sessions.backends.base import SessionBase

_THEME_VALUES: Final = ("light", "dark")
_FONT_SIZE_VALUES: Final = ("small", "medium", "large")
_SESSION_KEY: Final = "qbet.presentation"


@dataclass(frozen=True)
class PresentationPreferences:
    theme: str = "light"
    font_size: str = "medium"


def presentation_preferences(session: SessionBase) -> PresentationPreferences:
    """Read validated browser-session preferences with safe defaults."""

    values = session.get(_SESSION_KEY, {})
    theme = values.get("theme") if isinstance(values, dict) else None
    font_size = values.get("font_size") if isinstance(values, dict) else None
    return PresentationPreferences(
        theme=theme if theme in _THEME_VALUES else "light",
        font_size=font_size if font_size in _FONT_SIZE_VALUES else "medium",
    )


def save_presentation_preferences(
    session: SessionBase, preferences: PresentationPreferences
) -> None:
    """Persist presentation settings only for the current browser session."""

    session.modified = True
    session[_SESSION_KEY] = {
        "theme": preferences.theme,
        "font_size": preferences.font_size,
    }
