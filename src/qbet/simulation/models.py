"""Typed contracts for deterministic Q-Bet simulation runs."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, field_validator

from qbet.domain.models import DomainModel, Identifier, NonNegativeDecimal, PositiveDecimal


class SimulationEngine(StrEnum):
    """Engines that can participate in the shared simulation contract."""

    BASE = "base"
    YIELD = "yield"
    ALPHA = "alpha"


class SimulationStatus(StrEnum):
    """Observable lifecycle states for one synchronous simulation run."""

    PENDING = "pending"
    RUNNING = "running"
    STOPPED = "stopped"
    COMPLETED = "completed"


class SimulationEventType(StrEnum):
    RUN_STARTED = "run_started"
    STEP_COMPLETED = "step_completed"
    TOP_UP_APPLIED = "top_up_applied"
    STOP_REQUESTED = "stop_requested"
    DURATION_LIMIT_REACHED = "duration_limit_reached"
    RUN_STOPPED = "run_stopped"
    RUN_COMPLETED = "run_completed"


class SimulationTopUpEvent(DomainModel):
    """A simulated capital addition applied after a completed step count."""

    after_completed_steps: int = Field(ge=0)
    amount: PositiveDecimal


class SimulationRunConfig(DomainModel):
    """Validated inputs for a reproducible, bounded simulation run."""

    engine: SimulationEngine
    starting_capital: PositiveDecimal
    top_up_events: tuple[SimulationTopUpEvent, ...] = ()
    strategy_id: Identifier | None = None
    max_duration: timedelta = Field(default=timedelta(hours=24))

    @field_validator("max_duration")
    @classmethod
    def max_duration_is_bounded(cls, value: timedelta) -> timedelta:
        if value <= timedelta(0):
            raise ValueError("max_duration must be positive")
        if value > timedelta(hours=48):
            raise ValueError("max_duration must not exceed 48 hours")
        return value


class SimulationStep(DomainModel):
    """One mocked engine state transition in a simulation sequence."""

    id: Identifier
    capital_change: Decimal
    description: str = ""
    simulated_duration: timedelta = Field(default=timedelta(0))

    @field_validator("simulated_duration")
    @classmethod
    def simulated_duration_is_non_negative(cls, value: timedelta) -> timedelta:
        if value < timedelta(0):
            raise ValueError("simulated_duration must not be negative")
        return value


class SimulationContext(DomainModel):
    """Snapshot exposed at safe boundaries during a running simulation."""

    config: SimulationRunConfig
    status: SimulationStatus
    completed_step_count: int = Field(ge=0)
    current_capital: NonNegativeDecimal
    elapsed_duration: timedelta
    progress: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))


class SimulationEvent(DomainModel):
    """An auditable, simulation-only event produced by the runner."""

    sequence: int = Field(gt=0)
    event_type: SimulationEventType
    completed_step_count: int = Field(ge=0)
    current_capital: NonNegativeDecimal
    elapsed_duration: timedelta
    step_id: Identifier | None = None
    top_up_amount: PositiveDecimal | None = None


class SimulationResult(DomainModel):
    """The final immutable record for one deterministic simulation run."""

    config: SimulationRunConfig
    status: SimulationStatus
    completed_steps: tuple[SimulationStep, ...]
    current_capital: NonNegativeDecimal
    elapsed_duration: timedelta
    progress: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    events: tuple[SimulationEvent, ...]