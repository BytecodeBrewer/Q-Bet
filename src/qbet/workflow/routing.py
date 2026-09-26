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


class UserEngineModes(DomainModel):
    """Account-scoped route intent; it never controls execution authority."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    simulation: bool = False
    execution: bool = False


class UserRoutingPreferences(DomainModel):
    """Persisted user selection inside the global staff routing guardrails."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    bonus: UserEngineModes = Field(default_factory=UserEngineModes)
    sports_capital: UserEngineModes = Field(default_factory=UserEngineModes)


def connected_product_routing_configuration(
    configuration: RoutingConfiguration | None,
) -> RoutingConfiguration | None:
    """Mask routes whose provider-backed product path is not connected yet.

    Explicit static/injected configurations remain available to low-level regression
    seams; product composition uses this projection before scheduling new work.
    """

    if configuration is None:
        return None
    return RoutingConfiguration(
        bonus=EngineModes(
            simulation=configuration.bonus.simulation,
            execution=False,
            execution_sandbox=False,
        ),
        sports_capital=configuration.sports_capital,
    )


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


def user_engine_modes(
    preferences: UserRoutingPreferences,
    engine: V1Engine,
) -> UserEngineModes:
    if engine == "bonus":
        return preferences.bonus
    if engine == "sports_capital":
        return preferences.sports_capital
    raise ValueError("unsupported v1 engine")


def effective_engine_modes(
    configuration: RoutingConfiguration,
    preferences: UserRoutingPreferences,
    engine: V1Engine,
) -> EngineModes:
    """Intersect personal intent with the authoritative global availability."""

    global_modes = engine_modes(configuration, engine)
    selected_modes = user_engine_modes(preferences, engine)
    return EngineModes(
        simulation=global_modes.simulation and selected_modes.simulation,
        execution=global_modes.execution and selected_modes.execution,
        execution_sandbox=global_modes.execution_sandbox,
    )


def resolve_routes(
    configuration: RoutingConfiguration,
    engine: V1Engine,
    opportunity_id: str,
    correlation_id: UUID,
    owner: str,
    user_preferences: UserRoutingPreferences | None = None,
) -> tuple[RoutedWorkItem, ...]:
    modes = (
        effective_engine_modes(configuration, user_preferences, engine)
        if user_preferences is not None
        else engine_modes(configuration, engine)
    )
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
