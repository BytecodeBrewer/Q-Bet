"""Typed simulation report models and builders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from qbet.domain.models import DomainModel, Identifier
from qbet.layers.logging import SimulationLogRecord, SimulationLogRecordType
from qbet.simulation.models import (
    SimulationEvaluation,
    SimulationEvent,
    SimulationResult,
    SimulationRunConfig,
    SimulationStatus,
    SimulationStep,
)


class ReportDetailSelection(DomainModel):
    include_events: bool = False
    include_intermediate_results: bool = False
    include_raw_inputs: bool = False
    include_warnings: bool = False
    include_errors: bool = False


class SimulationReport(DomainModel):
    run_id: UUID
    config: SimulationRunConfig
    engine: Identifier
    strategy_id: Identifier | None
    status: SimulationStatus
    starting_capital: Decimal
    current_capital: Decimal
    profit_loss: Decimal
    completed_steps: tuple[SimulationStep, ...]
    elapsed_duration: timedelta
    progress: Decimal
    generated_at: datetime
    schema_version: int = Field(default=1, ge=1)
    events: tuple[SimulationEvent, ...] = ()
    intermediate_results: tuple[SimulationEvaluation, ...] = ()
    raw_input_snapshots: tuple[dict[str, object], ...] = ()
    warnings: tuple[SimulationLogRecord, ...] = ()
    errors: tuple[SimulationLogRecord, ...] = ()


class SimulationReportBuilder:
    """Builds compact or detail-selected reports from observational records."""

    def build(
        self,
        run_id: UUID,
        result: SimulationResult,
        records: tuple[SimulationLogRecord, ...],
        selection: ReportDetailSelection | None = None,
    ) -> SimulationReport:
        selection = selection or ReportDetailSelection()
        warnings = tuple(
            record
            for record in records
            if record.record_type is SimulationLogRecordType.WARNING
        )
        errors = tuple(
            record
            for record in records
            if record.record_type is SimulationLogRecordType.ERROR
        )
        raw_inputs = tuple(
            record.payload
            for record in records
            if record.record_type is SimulationLogRecordType.RAW_INPUT
        )
        events = tuple(
            SimulationEvent.model_validate(record.payload)
            for record in records
            if record.record_type is SimulationLogRecordType.EVENT
        )
        return SimulationReport(
            run_id=run_id,
            config=result.config,
            engine=result.config.engine,
            strategy_id=result.config.strategy_id,
            status=result.status,
            starting_capital=result.config.starting_capital,
            current_capital=result.current_capital,
            profit_loss=result.current_capital - result.config.starting_capital,
            completed_steps=result.completed_steps,
            elapsed_duration=result.elapsed_duration,
            progress=result.progress,
            generated_at=datetime.now(timezone.utc),
            events=events if selection.include_events else (),
            intermediate_results=(
                result.evaluations if selection.include_intermediate_results else ()
            ),
            raw_input_snapshots=raw_inputs if selection.include_raw_inputs else (),
            warnings=warnings if selection.include_warnings else (),
            errors=errors if selection.include_errors else (),
        )

    def rebuild(
        self,
        compact_report: SimulationReport,
        records: tuple[SimulationLogRecord, ...],
        selection: ReportDetailSelection,
    ) -> SimulationReport:
        """Regenerate selected detail from persisted records without rerunning a simulation."""

        result = SimulationResult(
            config=compact_report.config,
            status=compact_report.status,
            completed_steps=compact_report.completed_steps,
            evaluations=tuple(
                step.evaluation
                for step in compact_report.completed_steps
                if step.evaluation is not None
            ),
            current_capital=compact_report.current_capital,
            elapsed_duration=compact_report.elapsed_duration,
            progress=compact_report.progress,
            events=(),
        )
        return self.build(compact_report.run_id, result, records, selection)
