"""Synchronous deterministic simulation runner."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from qbet.simulation.models import (
    SimulationContext,
    SimulationEvaluation,
    SimulationEvent,
    SimulationEventType,
    SimulationResult,
    SimulationRunConfig,
    SimulationStatus,
    SimulationStep,
)


SimulationStepObserver = Callable[[SimulationContext], None]


class SimulationRunner(Protocol):
    """Contract used by engines and the future capital orchestrator."""

    def request_stop(self) -> None:
        """Request a stop at the next completed-step boundary."""
        ...

    def run(
        self,
        config: SimulationRunConfig,
        steps: Iterable[SimulationStep],
        *,
        on_step_completed: SimulationStepObserver | None = None,
    ) -> SimulationResult:
        """Run deterministic steps synchronously and return an immutable result."""
        ...


class DeterministicSimulationRunner:
    """Run ordered simulation steps without clocks, services, or external actions."""

    def __init__(self) -> None:
        self._stop_requested = False
        self._status = SimulationStatus.PENDING
        self._completed_steps: tuple[SimulationStep, ...] = ()
        self._current_capital = Decimal("0")
        self._elapsed_duration = timedelta(0)
        self._progress = Decimal("0")

    @property
    def status(self) -> SimulationStatus:
        return self._status

    @property
    def completed_steps(self) -> tuple[SimulationStep, ...]:
        return self._completed_steps

    @property
    def current_capital(self) -> Decimal:
        return self._current_capital

    @property
    def elapsed_duration(self) -> timedelta:
        return self._elapsed_duration

    @property
    def progress(self) -> Decimal:
        return self._progress

    def request_stop(self) -> None:
        """Record a stop request; the active step always finishes first."""

        self._stop_requested = True

    def run(
        self,
        config: SimulationRunConfig,
        steps: Iterable[SimulationStep],
        *,
        on_step_completed: SimulationStepObserver | None = None,
    ) -> SimulationResult:
        """Apply simulation capital changes in order and stop only at safe boundaries."""

        ordered_steps = tuple(steps)
        self._stop_requested = False
        self._status = SimulationStatus.RUNNING
        self._completed_steps = ()
        self._current_capital = Decimal(config.starting_capital)
        self._elapsed_duration = timedelta(0)
        self._progress = Decimal("0")
        events: list[SimulationEvent] = []
        evaluations: list[SimulationEvaluation] = []

        def record(
            event_type: SimulationEventType,
            *,
            step_id: str | None = None,
            top_up_amount: Decimal | None = None,
        ) -> None:
            events.append(
                SimulationEvent(
                    sequence=len(events) + 1,
                    event_type=event_type,
                    completed_step_count=len(self._completed_steps),
                    current_capital=self._current_capital,
                    elapsed_duration=self._elapsed_duration,
                    step_id=step_id,
                    top_up_amount=top_up_amount,
                )
            )

        def apply_top_ups() -> None:
            for top_up in config.top_up_events:
                if top_up.after_completed_steps == len(self._completed_steps):
                    self._current_capital += top_up.amount
                    record(SimulationEventType.TOP_UP_APPLIED, top_up_amount=top_up.amount)

        record(SimulationEventType.RUN_STARTED)
        apply_top_ups()

        for step in ordered_steps:
            remaining_duration = config.max_duration - self._elapsed_duration
            if step.simulated_duration > remaining_duration:
                record(SimulationEventType.DURATION_LIMIT_REACHED)
                self._status = SimulationStatus.STOPPED
                record(SimulationEventType.RUN_STOPPED)
                break

            next_capital = self._current_capital + step.capital_change
            if next_capital < Decimal("0"):
                raise ValueError("simulation step would make simulated capital negative")

            self._current_capital = next_capital
            self._elapsed_duration += step.simulated_duration
            self._completed_steps = (*self._completed_steps, step)
            if step.evaluation is not None:
                evaluations.append(step.evaluation)
            self._progress = Decimal(len(self._completed_steps)) / Decimal(len(ordered_steps))
            record(SimulationEventType.STEP_COMPLETED, step_id=step.id)
            apply_top_ups()

            if on_step_completed is not None:
                on_step_completed(self._context(config))
            if self._stop_requested:
                record(SimulationEventType.STOP_REQUESTED)
                self._status = SimulationStatus.STOPPED
                record(SimulationEventType.RUN_STOPPED)
                break
        else:
            self._status = SimulationStatus.COMPLETED
            self._progress = Decimal("1")
            record(SimulationEventType.RUN_COMPLETED)

        return SimulationResult(
            config=config,
            status=self._status,
            completed_steps=self._completed_steps,
            evaluations=tuple(evaluations),
            current_capital=self._current_capital,
            elapsed_duration=self._elapsed_duration,
            progress=self._progress,
            events=tuple(events),
        )

    def _context(self, config: SimulationRunConfig) -> SimulationContext:
        return SimulationContext(
            config=config,
            status=self._status,
            completed_step_count=len(self._completed_steps),
            current_capital=self._current_capital,
            elapsed_duration=self._elapsed_duration,
            progress=self._progress,
        )
