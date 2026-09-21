from uuid import UUID

from qbet.workflow.models import WorkflowMode
from qbet.workflow.orchestrator import WorkflowOrchestrator
from qbet.workflow.routing import (
    EngineModes,
    RoutingConfiguration,
    UserEngineModes,
    UserRoutingPreferences,
)

CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def test_user_selection_is_intersected_with_global_staff_availability() -> None:
    orchestrator = WorkflowOrchestrator(
        routing_configuration=RoutingConfiguration(
            bonus=EngineModes(simulation=True, execution=False)
        ),
        user_routing_preferences_loader=lambda owner: UserRoutingPreferences(
            bonus=UserEngineModes(simulation=True, execution=True)
        ),
    )

    routes = orchestrator.route("bonus", "opportunity", CORRELATION_ID, "alice")

    assert tuple(route.mode for route in routes) == (WorkflowMode.SIMULATION,)


def test_missing_user_selection_fails_closed_when_preference_loader_is_active() -> None:
    orchestrator = WorkflowOrchestrator(
        routing_configuration=RoutingConfiguration(
            bonus=EngineModes(simulation=True, execution=True)
        ),
        user_routing_preferences_loader=lambda owner: UserRoutingPreferences(),
    )

    assert orchestrator.route("bonus", "opportunity", CORRELATION_ID, "alice") == ()
