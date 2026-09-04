"""Session-scoped presentation and dashboard layout settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from django.contrib.sessions.backends.base import SessionBase

_THEME_VALUES: Final = ("light", "dark")
_FONT_SIZE_VALUES: Final = ("small", "medium", "large")
_PRESENTATION_SESSION_KEY: Final = "qbet.presentation"
_LAYOUT_SESSION_KEY: Final = "qbet.dashboard-layout"
_WIDGET_ORDER: Final = ("bonus", "sports_capital")
_LAYOUT_PLANES: Final = ("execution", "simulation")


@dataclass(frozen=True)
class PresentationPreferences:
    theme: str = "light"
    font_size: str = "medium"


@dataclass(frozen=True)
class DashboardLayout:
    execution: tuple[str, ...] = _WIDGET_ORDER
    simulation: tuple[str, ...] = _WIDGET_ORDER


def presentation_preferences(session: SessionBase) -> PresentationPreferences:
    """Read validated browser-session preferences with safe defaults."""

    values = session.get(_PRESENTATION_SESSION_KEY, {})
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
    session[_PRESENTATION_SESSION_KEY] = {
        "theme": preferences.theme,
        "font_size": preferences.font_size,
    }


def dashboard_layout(session: SessionBase) -> DashboardLayout:
    """Read a validated per-plane widget order from the current session."""

    values = session.get(_LAYOUT_SESSION_KEY, {})
    execution = _validated_widget_order(
        values.get("execution") if isinstance(values, dict) else None
    )
    simulation = _validated_widget_order(
        values.get("simulation") if isinstance(values, dict) else None
    )
    return DashboardLayout(execution=execution, simulation=simulation)


def save_dashboard_widget_order(
    session: SessionBase,
    *,
    plane: str,
    order: tuple[str, ...],
) -> DashboardLayout:
    """Persist one validated widget order without crossing dashboard planes."""

    if plane not in _LAYOUT_PLANES:
        raise ValueError("Unknown dashboard plane.")
    validated = _validated_widget_order(order, strict=True)
    current = dashboard_layout(session)
    updated = DashboardLayout(
        execution=validated if plane == "execution" else current.execution,
        simulation=validated if plane == "simulation" else current.simulation,
    )
    session.modified = True
    session[_LAYOUT_SESSION_KEY] = {
        "execution": list(updated.execution),
        "simulation": list(updated.simulation),
    }
    return updated


def _validated_widget_order(
    value: object,
    *,
    strict: bool = False,
) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        order = tuple(str(item) for item in value)
        if len(order) == len(_WIDGET_ORDER) and set(order) == set(_WIDGET_ORDER):
            return order
    if strict:
        raise ValueError("Dashboard order must contain each v1 engine exactly once.")
    return _WIDGET_ORDER
