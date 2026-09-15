from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from qbet.workflow.models import WorkflowMode
from qbet.workflow.routing import (
    EngineModes,
    RoutingConfiguration,
    V1Engine,
    resolve_routes,
)

CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def _configuration(engine: V1Engine, modes: EngineModes) -> RoutingConfiguration:
    if engine == "bonus":
        return RoutingConfiguration(bonus=modes)
    return RoutingConfiguration(sports_capital=modes)


@pytest.mark.parametrize("engine", ("bonus", "sports_capital"))
@pytest.mark.parametrize(
    ("simulation", "execution", "expected_modes"),
    (
        (False, False, ()),
        (True, False, (WorkflowMode.SIMULATION,)),
        (False, True, (WorkflowMode.EXECUTION,)),
        (True, True, (WorkflowMode.SIMULATION, WorkflowMode.EXECUTION)),
    ),
)
def test_each_v1_engine_supports_all_routing_outcomes(
    engine: V1Engine,
    simulation: bool,
    execution: bool,
    expected_modes: tuple[WorkflowMode, ...],
) -> None:
    configuration = _configuration(
        engine,
        EngineModes(simulation=simulation, execution=execution),
    )

    routes = resolve_routes(
        configuration,
        engine,
        "opportunity",
        CORRELATION_ID,
        "owner",
    )

    assert tuple(route.mode for route in routes) == expected_modes
    assert all(route.engine == engine for route in routes)
    assert all(route.correlation_id == CORRELATION_ID for route in routes)


def test_dual_mode_routes_are_deterministic_and_isolated() -> None:
    configuration = RoutingConfiguration(
        bonus=EngineModes(simulation=True, execution=True),
    )

    first = resolve_routes(
        configuration,
        "bonus",
        "opportunity",
        CORRELATION_ID,
        "owner",
    )
    second = resolve_routes(
        configuration,
        "bonus",
        "opportunity",
        CORRELATION_ID,
        "owner",
    )

    assert first == second
    assert len(first) == 2
    assert first[0].id != first[1].id
    assert first[0].capital_context != first[1].capital_context


def test_unknown_v1_engine_is_rejected_even_when_all_modes_are_disabled() -> None:
    with pytest.raises(ValueError, match="unsupported v1 engine"):
        resolve_routes(
            RoutingConfiguration(),
            cast(V1Engine, "ticket"),
            "opportunity",
            CORRELATION_ID,
            "owner",
        )


def test_routing_models_reject_unknown_configuration_fields() -> None:
    with pytest.raises(ValidationError):
        RoutingConfiguration.model_validate(
            {
                "bonus": {},
                "sports_capital": {},
                "ticket": {"simulation": True},
            }
        )
    with pytest.raises(ValidationError):
        EngineModes.model_validate({"simulation": True, "live": True})


def test_execution_routes_preserve_the_configured_sandbox_guard() -> None:
    routes = resolve_routes(
        RoutingConfiguration(bonus=EngineModes(execution=True, execution_sandbox=False)),
        "bonus",
        "opportunity",
        CORRELATION_ID,
        "owner",
    )

    assert len(routes) == 1
    assert routes[0].mode is WorkflowMode.EXECUTION
    assert not routes[0].execution_sandbox
