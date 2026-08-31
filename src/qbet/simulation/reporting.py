"""Simulation integration for observational logging and report generation."""

from __future__ import annotations

from collections.abc import Iterable

from qbet.layers.logging import (
    SimulationLogContext,
    SimulationLogRecord,
    SimulationLogRecordType,
)
from qbet.reporting import SimulationReport, SimulationReportBuilder
from qbet.simulation.models import (
    SimulationContext,
    SimulationEvent,
    SimulationEventType,
    SimulationResult,
    SimulationRunConfig,
    SimulationStep,
)
from qbet.simulation.runner import (
    DeterministicSimulationRunner,
    SimulationStepGate,
    SimulationStepObserver,
)
from qbet.storage import SimulationReportStore


class ReportingSimulationRunner:
    """Observes deterministic runs and exposes a compact report afterward."""

    def __init__(self, store: SimulationReportStore | None = None) -> None:
        self._runner = DeterministicSimulationRunner()
        self._store = store
        self.last_report: SimulationReport | None = None
        self.last_records: tuple[SimulationLogRecord, ...] = ()

    def request_stop(self) -> None:
        self._runner.request_stop()

    def run(
        self,
        config: SimulationRunConfig,
        steps: Iterable[SimulationStep],
        *,
        on_step_completed: SimulationStepObserver | None = None,
        on_step_ready: SimulationStepGate | None = None,
        log_context: SimulationLogContext | None = None,
    ) -> SimulationResult:
        ordered_steps = tuple(steps)
        context = log_context or SimulationLogContext(on_record=self._append_record)
        context.record(
            SimulationLogRecordType.RAW_INPUT,
            "simulation.config",
            {"config": config.model_dump(mode="json")},
        )

        def observe_event(event: SimulationEvent) -> None:
            record_type = (
                SimulationLogRecordType.RUN_STARTED
                if event.event_type is SimulationEventType.RUN_STARTED
                else SimulationLogRecordType.EVENT
            )
            context.record(
                record_type,
                "simulation.runner",
                event.model_dump(mode="json"),
            )
            if event.event_type is SimulationEventType.TOP_UP_APPLIED:
                context.record(
                    SimulationLogRecordType.CAPITAL_TRANSITION,
                    "simulation.runner",
                    {
                        "movement_type": "top_up",
                        "capital_change": str(event.top_up_amount),
                        "current_capital": str(event.current_capital),
                    },
                )

        def observe_step_applied(boundary: SimulationContext) -> None:
            step = ordered_steps[boundary.completed_step_count - 1]
            context.record(
                SimulationLogRecordType.CAPITAL_TRANSITION,
                "simulation.runner",
                {
                    "movement_type": "step",
                    "step_id": step.id,
                    "capital_change": str(step.capital_change),
                    "current_capital": str(boundary.current_capital),
                },
            )
            context.record(
                SimulationLogRecordType.INTERMEDIATE_RESULT,
                "simulation.runner",
                {
                    "step_id": step.id,
                    "completed_step_count": boundary.completed_step_count,
                },
            )
            if step.evaluation is not None:
                context.record(
                    SimulationLogRecordType.EVALUATION,
                    "simulation.engine",
                    step.evaluation.model_dump(mode="json"),
                )

        try:
            result = self._runner.run(
                config,
                ordered_steps,
                on_step_completed=on_step_completed,
                on_step_applied=observe_step_applied,
                on_step_ready=on_step_ready,
                on_event=observe_event,
            )
        except Exception as error:
            context.record(
                SimulationLogRecordType.ERROR,
                "simulation.runner",
                {"message": str(error)},
            )
            self.last_records = context.records
            raise

        if result.status.value == "stopped":
            context.record(
                SimulationLogRecordType.WARNING,
                "simulation.runner",
                {"status": result.status.value},
            )
        context.record(
            SimulationLogRecordType.RUN_FINISHED,
            "simulation.runner",
            {
                "status": result.status.value,
                "current_capital": str(result.current_capital),
            },
        )
        self.last_records = context.records
        self.last_report = SimulationReportBuilder().build(
            context.run_id,
            result,
            self.last_records,
        )
        if self._store is not None:
            self._store.finalize_run(self.last_report)
        return result

    def _append_record(self, record: SimulationLogRecord) -> None:
        if self._store is not None:
            self._store.append_records((record,))
