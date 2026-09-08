"""Administrator engine/mode routing controls backed by PostgreSQL."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from qbet.storage.ledger import (
    RoutingConfigurationPersistenceError,
    RoutingConfigurationRepository,
)
from qbet.web.controls import presentation_preferences
from qbet.web.forms import RoutingConfigurationForm
from qbet.workflow.routing import EngineModes, RoutingConfiguration


def _is_staff(user: object) -> bool:
    return bool(getattr(user, "is_staff", False))


def _mode_summary(modes: EngineModes) -> str:
    if modes.simulation and modes.execution:
        return "Simulation and Execution"
    if modes.simulation:
        return "Simulation only"
    if modes.execution:
        return "Execution only"
    return "Inactive"


def _render_settings(
    request: HttpRequest,
    *,
    configuration: RoutingConfiguration | None,
    form: RoutingConfigurationForm | None,
    persisted: bool,
    available: bool,
    error: bool = False,
    status: int = 200,
) -> HttpResponse:
    summary = (
        {
            "bonus": _mode_summary(configuration.bonus),
            "sports_capital": _mode_summary(configuration.sports_capital),
        }
        if configuration is not None
        else {}
    )
    return render(
        request,
        "qbet_web/admin_gui_settings.html",
        {
            "preferences": presentation_preferences(request.session),
            "routing_configuration": configuration,
            "routing_form": form,
            "routing_summary": summary,
            "routing_persisted": persisted,
            "routing_available": available,
            "routing_error": error,
        },
        status=status,
    )


@user_passes_test(_is_staff, login_url="login")
@require_http_methods(["GET", "POST"])
def routing_settings(request: HttpRequest) -> HttpResponse:
    repository = RoutingConfigurationRepository()
    try:
        stored = repository.load()
    except RoutingConfigurationPersistenceError:
        return _render_settings(
            request,
            configuration=None,
            form=None,
            persisted=False,
            available=False,
            error=True,
            status=503,
        )

    current = stored or RoutingConfiguration()
    persisted = stored is not None

    if request.method == "POST":
        form = RoutingConfigurationForm(request.POST)
        if not form.is_valid():
            return _render_settings(
                request,
                configuration=current,
                form=form,
                persisted=persisted,
                available=True,
                status=400,
            )
        candidate = form.to_configuration()
        try:
            repository.save(candidate)
        except RoutingConfigurationPersistenceError:
            return _render_settings(
                request,
                configuration=current,
                form=form,
                persisted=persisted,
                available=True,
                error=True,
                status=503,
            )
        messages.success(request, "Engine routing configuration updated.")
        return redirect("admin-gui-settings")

    form = RoutingConfigurationForm(
        initial=RoutingConfigurationForm.initial_from_configuration(current)
    )
    return _render_settings(
        request,
        configuration=current,
        form=form,
        persisted=persisted,
        available=True,
    )
