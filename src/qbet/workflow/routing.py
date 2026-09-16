"""Deterministic engine/mode fan-out without shared mutable work items."""

from typing import Literal
from uuid import UUID, uuid5

from pydantic import ConfigDict, Field

from qbet.domain.models import DomainModel
from qbet.workflow.models import WorkflowMode

V1Engine = Literal["bonus", "sports_capital"]
V1_ENGINES: tuple[V1Engine, ...] = ("bonus", "sports_capital")


class EngineModes(DomainModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    simulation: bool = False
    execution: bool = False
    execution_sandbox: bool = True


class RoutingConfiguration(DomainModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    bonus: EngineModes = Field(default_factory=EngineModes)
    sports_capital: EngineModes = Field(default_factory=EngineModes)


class RoutedWorkItem(DomainModel):
    id: UUID
    correlation_id: UUID
    engine: V1Engine
    mode: WorkflowMode
    opportunity_id: str = Field(min_length=1)
    capital_context: str
    owner: str | None = None
    execution_sandbox: bool = False


def engine_modes(configuration: RoutingConfiguration, engine: V1Engine) -> EngineModes:
    if engine == "bonus":
        return configuration.bonus
    if engine == "sports_capital":
        return configuration.sports_capital
    raise ValueError("unsupported v1 engine")


def resolve_routes(
    configuration: RoutingConfiguration,
    engine: V1Engine,
    opportunity_id: str,
    correlation_id: UUID,
    owner: str,
) -> tuple[RoutedWorkItem, ...]:
    modes = engine_modes(configuration, engine)
    return tuple(
        RoutedWorkItem(
            id=uuid5(correlation_id, f"{owner}:{engine}:{mode.value}:{opportunity_id}"),
            correlation_id=correlation_id,
            engine=engine,
            mode=mode,
            opportunity_id=opportunity_id,
            capital_context=f"{owner}:{mode.value}",
            owner=owner,
            execution_sandbox=(
                modes.execution_sandbox if mode is WorkflowMode.EXECUTION else False
            ),
        )
        for mode, enabled in (
            (WorkflowMode.SIMULATION, modes.simulation),
            (WorkflowMode.EXECUTION, modes.execution),
        )
        if enabled
    )
