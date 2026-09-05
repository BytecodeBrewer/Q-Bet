from uuid import uuid4

from qbet.workflow.models import WorkflowMode
from qbet.workflow.routing import EngineModes, RoutingConfiguration, resolve_routes


def test_both_modes_create_distinct_isolated_work_items():
    correlation_id = uuid4()
    routes = resolve_routes(
        RoutingConfiguration(
            bonus=EngineModes(simulation=True, execution=True),
        ),
        "bonus",
        "opportunity",
        correlation_id,
        "run",
    )
    assert tuple(item.mode for item in routes) == (
        WorkflowMode.SIMULATION,
        WorkflowMode.EXECUTION,
    )
    assert routes[0].id != routes[1].id
    assert routes[0].capital_context != routes[1].capital_context
    assert all(item.correlation_id == correlation_id for item in routes)


def test_disabled_mode_is_not_routed_and_resolution_is_repeatable():
    correlation_id = uuid4()
    config = RoutingConfiguration(
        sports_capital=EngineModes(simulation=False, execution=True)
    )
    first = resolve_routes(config, "sports_capital", "opportunity", correlation_id, "run")
    second = resolve_routes(config, "sports_capital", "opportunity", correlation_id, "run")
    assert len(first) == 1
    assert first == second
    assert first[0].mode is WorkflowMode.EXECUTION
