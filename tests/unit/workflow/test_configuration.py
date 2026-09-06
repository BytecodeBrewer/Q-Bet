from uuid import uuid4

from qbet.workflow.models import WorkflowMode
from qbet.workflow.orchestrator import WorkflowOrchestrator
from qbet.workflow.routing import EngineModes, RoutingConfiguration


def test_orchestrator_uses_persistable_configuration_for_routes():
    orchestrator = WorkflowOrchestrator(
        routing_configuration=RoutingConfiguration(
            bonus=EngineModes(simulation=True, execution=False)
        )
    )
    routes = orchestrator.route("bonus", "opportunity", uuid4(), "run")
    assert len(routes) == 1
    assert routes[0].mode is WorkflowMode.SIMULATION
