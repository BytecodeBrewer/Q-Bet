"""Deterministic engine/mode fan-out without shared mutable work items."""

from typing import Literal
from uuid import UUID, uuid5

from pydantic import Field

from qbet.domain.models import DomainModel
from qbet.workflow.models import WorkflowMode

V1Engine = Literal["bonus", "sports_capital"]


class EngineModes(DomainModel):
    simulation: bool = False
    execution: bool = False


class RoutingConfiguration(DomainModel):
    bonus: EngineModes = Field(default_factory=EngineModes)
    sports_capital: EngineModes = Field(default_factory=EngineModes)


class RoutedWorkItem(DomainModel):
    id: UUID
    correlation_id: UUID
    engine: V1Engine
    mode: WorkflowMode
    opportunity_id: str = Field(min_length=1)
    capital_context: str


def resolve_routes(
    configuration: RoutingConfiguration,
    engine: V1Engine,
    opportunity_id: str,
    correlation_id: UUID,
    owner: str,
) -> tuple[RoutedWorkItem, ...]:
    modes = configuration.bonus if engine == "bonus" else configuration.sports_capital
    return tuple(
        RoutedWorkItem(
            id=uuid5(correlation_id, f"{owner}:{engine}:{mode.value}:{opportunity_id}"),
            correlation_id=correlation_id,
            engine=engine,
            mode=mode,
            opportunity_id=opportunity_id,
            capital_context=f"{owner}:{mode.value}",
        )
        for mode, enabled in (
            (WorkflowMode.SIMULATION, modes.simulation),
            (WorkflowMode.EXECUTION, modes.execution),
        )
        if enabled
    )
