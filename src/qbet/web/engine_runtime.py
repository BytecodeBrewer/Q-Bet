"""Administrator-only runtime start/stop controls for the two Phase-2 engines."""

from __future__ import annotations

from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from qbet.storage.ledger import (
    RoutingConfigurationPersistenceError,
    RoutingConfigurationRepository,
)
from qbet.web.simulation_control import SimulationControlService
from qbet.workflow.models import WorkflowMode
from qbet.workflow.routing import V1_ENGINES, V1Engine


@login_required
@require_POST
def engine_runtime_control(
    request: HttpRequest,
    engine_id: str,
    mode: str,
    action: str,
) -> HttpResponse:
    """Toggle runtime eligibility only; this never creates work or moves capital."""

    if not bool(getattr(request.user, "is_staff", False)):
        raise Http404("Engine control is not available.")
    if engine_id not in V1_ENGINES:
        raise Http404("Engine not found.")
    try:
        workflow_mode = WorkflowMode(mode)
    except ValueError as error:
        raise Http404("Mode not found.") from error
    if action not in {"start", "stop"}:
        raise Http404("Action not found.")

    if workflow_mode is WorkflowMode.SIMULATION:
        if action == "start" and not SimulationControlService().availability().enabled:
            messages.error(request, "Simulation is disabled.")
            return redirect("dashboard")

    try:
        RoutingConfigurationRepository().set_mode_active(
            engine=cast(V1Engine, engine_id),
            mode=workflow_mode,
            active=action == "start",
        )
    except RoutingConfigurationPersistenceError:
        messages.error(request, "Engine control is temporarily unavailable.")
        return redirect("dashboard")

    state = "started" if action == "start" else "stopped"
    messages.success(request, f"{engine_id} {workflow_mode.value} {state}.")
    return redirect("dashboard")


@login_required
@require_POST
def sandbox_execution_control(
    request: HttpRequest,
    engine_id: str,
    action: str,
) -> HttpResponse:
    """Administrator-only control for deterministic sandbox Execution."""

    if not bool(getattr(request.user, "is_staff", False)):
        raise Http404("Sandbox execution control is not available.")
    if engine_id not in V1_ENGINES or action not in {"start", "stop"}:
        raise Http404("Sandbox execution control was not found.")
    try:
        RoutingConfigurationRepository().set_mode_active(
            engine=cast(V1Engine, engine_id),
            mode=WorkflowMode.EXECUTION,
            active=action == "start",
        )
    except RoutingConfigurationPersistenceError:
        messages.error(request, "Sandbox execution control is temporarily unavailable.")
        return redirect("dashboard")
    messages.success(request, f"{engine_id} sandbox execution {action}ed.")
    return redirect("dashboard")
