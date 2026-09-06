"""Typed simulation report models and builders."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
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
    include_risk_decisions: bool = False
    include_workflow_transitions: bool = False


class CustomerReportAmount(DomainModel):
    """One business-facing allocation recorded on a completed result."""

    label: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(ge=Decimal(0), allow_inf_nan=False)


class CustomerReportInput(DomainModel):
    """Business data required before a completed result can become a customer report."""

    match: str = Field(min_length=1, max_length=255)
    provider: str = Field(min_length=1, max_length=120)
    counterparty_provider: str = Field(min_length=1, max_length=120)
    strategy: str = Field(min_length=1, max_length=120)
    assigned_amounts: tuple[CustomerReportAmount, ...] = Field(min_length=1)
    invested_capital: Decimal = Field(gt=Decimal(0), allow_inf_nan=False)
    result_state: Literal["completed"] = "completed"
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    transaction_id: Identifier | None = None


class CompletedStepSummary(DomainModel):
    """Compact, evaluation-free description of one completed simulation step."""

    id: Identifier
    capital_change: Decimal
    simulated_duration: timedelta

    @classmethod
    def from_step(cls, step: SimulationStep) -> CompletedStepSummary:
        return cls(
            id=step.id,
            capital_change=step.capital_change,
            simulated_duration=step.simulated_duration,
        )


class SimulationReport(DomainModel):
    run_id: UUID
    config: SimulationRunConfig
    engine: Identifier
    strategy_id: Identifier | None
    status: SimulationStatus
    starting_capital: Decimal
    current_capital: Decimal
    top_up_total: Decimal
    profit_loss: Decimal
    completed_steps: tuple[CompletedStepSummary, ...]
    elapsed_duration: timedelta
    progress: Decimal
    generated_at: datetime
    schema_version: int = Field(default=1, ge=1)
    events: tuple[SimulationEvent, ...] = ()
    intermediate_results: tuple[SimulationEvaluation, ...] = ()
    raw_input_snapshots: tuple[dict[str, Any], ...] = ()
    warnings: tuple[SimulationLogRecord, ...] = ()
    errors: tuple[SimulationLogRecord, ...] = ()
    risk_decisions: tuple[SimulationLogRecord, ...] = ()
    workflow_transitions: tuple[SimulationLogRecord, ...] = ()
    customer_report_input: CustomerReportInput | None = None


class SimulationReportBuilder:
    """Builds compact or detail-selected reports from observational records."""

    def build(
        self,
        run_id: UUID,
        result: SimulationResult,
        records: tuple[SimulationLogRecord, ...],
        selection: ReportDetailSelection | None = None,
        customer_report_input: CustomerReportInput | None = None,
    ) -> SimulationReport:
        return self._build(
            run_id=run_id,
            config=result.config,
            status=result.status,
            current_capital=result.current_capital,
            completed_steps=tuple(
                CompletedStepSummary.from_step(step) for step in result.completed_steps
            ),
            elapsed_duration=result.elapsed_duration,
            progress=result.progress,
            records=records,
            selection=selection or ReportDetailSelection(),
            customer_report_input=customer_report_input,
        )

    def rebuild(
        self,
        compact_report: SimulationReport,
        records: tuple[SimulationLogRecord, ...],
        selection: ReportDetailSelection,
    ) -> SimulationReport:
        """Regenerate selected detail from persisted records without rerunning a simulation."""

        return self._build(
            run_id=compact_report.run_id,
            config=compact_report.config,
            status=compact_report.status,
            current_capital=compact_report.current_capital,
            completed_steps=compact_report.completed_steps,
            elapsed_duration=compact_report.elapsed_duration,
            progress=compact_report.progress,
            records=records,
            selection=selection,
            generated_at=compact_report.generated_at,
            customer_report_input=compact_report.customer_report_input,
        )

    def _build(
        self,
        *,
        run_id: UUID,
        config: SimulationRunConfig,
        status: SimulationStatus,
        current_capital: Decimal,
        completed_steps: tuple[CompletedStepSummary, ...],
        elapsed_duration: timedelta,
        progress: Decimal,
        records: tuple[SimulationLogRecord, ...],
        selection: ReportDetailSelection,
        generated_at: datetime | None = None,
        customer_report_input: CustomerReportInput | None = None,
    ) -> SimulationReport:
        warnings = tuple(
            record for record in records if record.record_type is SimulationLogRecordType.WARNING
        )
        errors = tuple(
            record for record in records if record.record_type is SimulationLogRecordType.ERROR
        )
        risk_decisions = tuple(
            record
            for record in records
            if record.record_type is SimulationLogRecordType.RISK_DECISION
        )
        workflow_transitions = tuple(
            record
            for record in records
            if record.record_type is SimulationLogRecordType.WORKFLOW_TRANSITION
        )
        raw_inputs = tuple(
            record.payload
            for record in records
            if record.record_type is SimulationLogRecordType.RAW_INPUT
        )
        events = tuple(
            SimulationEvent.model_validate(record.payload)
            for record in records
            if record.record_type
            in (SimulationLogRecordType.RUN_STARTED, SimulationLogRecordType.EVENT)
            and "event_type" in record.payload
        )
        top_up_total = sum(
            (
                event.top_up_amount or Decimal(0)
                for event in events
                if event.event_type.value == "top_up_applied"
            ),
            Decimal(0),
        )
        evaluations = tuple(
            SimulationEvaluation.model_validate(record.payload)
            for record in records
            if record.record_type is SimulationLogRecordType.EVALUATION
        )
        return SimulationReport(
            run_id=run_id,
            config=config,
            engine=config.engine,
            strategy_id=config.strategy_id,
            status=status,
            starting_capital=config.starting_capital,
            current_capital=current_capital,
            top_up_total=top_up_total,
            profit_loss=current_capital - config.starting_capital - top_up_total,
            completed_steps=completed_steps,
            elapsed_duration=elapsed_duration,
            progress=progress,
            generated_at=generated_at or datetime.now(UTC),
            events=events if selection.include_events else (),
            intermediate_results=(evaluations if selection.include_intermediate_results else ()),
            raw_input_snapshots=raw_inputs if selection.include_raw_inputs else (),
            warnings=warnings if selection.include_warnings else (),
            errors=errors if selection.include_errors else (),
            risk_decisions=risk_decisions if selection.include_risk_decisions else (),
            workflow_transitions=(
                workflow_transitions if selection.include_workflow_transitions else ()
            ),
            customer_report_input=customer_report_input,
        )
