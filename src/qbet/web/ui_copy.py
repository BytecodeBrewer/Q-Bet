"""Small localized copy set for the authenticated application chrome."""

from __future__ import annotations

from typing import Final

_COPY: Final = {
    "en": {
        "dashboard": "Main Dashboard",
        "execution_engine": "Execution Engine",
        "reports": "Reports",
        "simulation": "Simulation",
        "simulation_dashboard": "Simulation Dashboard",
        "settings": "Settings",
        "notifications": "Notifications",
        "approvals": "Approvals",
        "profile": "Profile & account",
        "sign_out": "Sign out",
        "staff_tools": "Staff tools",
        "monitoring": "Monitoring",
        "preferred_currency": "Preferred recorded currency",
    },
    "de": {
        "dashboard": "Übersicht / Main Dashboard",
        "execution_engine": "Execution Engine",
        "reports": "Berichte / Reports",
        "simulation": "Simulation",
        "simulation_dashboard": "Simulation Übersicht / Simulation Dashboard",
        "settings": "Einstellungen / Settings",
        "notifications": "Benachrichtigungen / Notifications",
        "approvals": "Freigaben / Approvals",
        "profile": "Profil & Konto / Profile & account",
        "sign_out": "Abmelden / Sign out",
        "staff_tools": "Admin-Werkzeuge / Staff tools",
        "monitoring": "Monitoring",
        "preferred_currency": "Bevorzugte aufgezeichnete Währung",
    },
}


def ui_copy(language: str) -> dict[str, str]:
    """Return only the deliberately supported shell localization surface."""

    return dict(_COPY.get(language, _COPY["en"]))
