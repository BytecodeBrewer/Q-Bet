from uuid import UUID

import pytest

from qbet.workflow.models import WorkflowMode
from qbet.workflow.orchestrator import WorkflowOrchestrator
from qbet.workflow.routing import EngineModes, RoutingConfiguration

CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def test_orchestrator_uses_persistable_configuration_for_routes() -> None:
    orchestrator = WorkflowOrchestrator(
        routing_configuration=RoutingConfiguration(
            bonus=EngineModes(simulation=True, execution=False)
        )
    )
    routes = orchestrator.route("bonus", "opportunity", CORRELATION_ID, "run")
    assert len(routes) == 1
    assert routes[0].mode is WorkflowMode.SIMULATION


def test_orchestrator_loads_fresh_routing_configuration_for_each_route() -> None:
    configurations = iter(
        (
            RoutingConfiguration(bonus=EngineModes(simulation=True)),
            RoutingConfiguration(bonus=EngineModes(execution=True)),
        )
    )
    orchestrator = WorkflowOrchestrator(
        routing_configuration_loader=lambda: next(configurations)
    )

    first = orchestrator.route("bonus", "opportunity-1", CORRELATION_ID, "run")
    second = orchestrator.route("bonus", "opportunity-2", CORRELATION_ID, "run")

    assert tuple(route.mode for route in first) == (WorkflowMode.SIMULATION,)
    assert tuple(route.mode for route in second) == (WorkflowMode.EXECUTION,)


def test_missing_durable_configuration_fails_closed_to_no_routes() -> None:
    orchestrator = WorkflowOrchestrator(routing_configuration_loader=lambda: None)

    assert orchestrator.route("bonus", "opportunity", CORRELATION_ID, "run") == ()


def test_static_and_dynamic_routing_configuration_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        WorkflowOrchestrator(
            routing_configuration=RoutingConfiguration(),
            routing_configuration_loader=lambda: RoutingConfiguration(),
        )
