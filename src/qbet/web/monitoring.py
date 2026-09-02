"""Read-only GUI monitoring models built from persisted simulation reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from qbet.layers.logging import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import (
    ReportDetailSelection,
    SimulationReport,
    SimulationReportBuilder,
)
from qbet.simulation import SimulationStatus
from qbet.storage import SimulationReportReader
from qbet.workflow import WorkflowDecision, WorkflowStage

_REPORT_LIMIT: Final = 50
_V1_ENGINES: Final = (
    ("BonusEngine", "bonus"),
    ("SportsCapitalEngine", "sports_capital"),
)
_WORKFLOW_STAGES: Final = (
    (WorkflowStage.DATA_AGGREGATION, "Data aggregation"),
    (WorkflowStage.ENGINE_PREPARATION, "Engine preparation"),
    (WorkflowStage.CALCULATION, "Calculation"),
    (WorkflowStage.DOMAIN_RISK, "Domain risk"),
    (WorkflowStage.LIQUIDITY_CHECK, "LiquidityChecker"),
    (WorkflowStage.DISPATCH, "Dispatch"),
)


@dataclass(frozen=True)
class MonitoringWorkflowStage:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class MonitoringEngineStatus:
    name: str
    status: str
    detail: str
    engine_id: str = ""
    active: bool = False
    mode: str = "unavailable"
    live_state: str = "unavailable"
    running_matches: int = 0
    pending_matches: int = 0
    total_activity: int = 0
    involved_capital: Decimal | None = None
    warning_count: int = 0
    error_count: int = 0
    latest_report_id: UUID | None = None
    workflow_stages: tuple[MonitoringWorkflowStage, ...] = ()


@dataclass(frozen=True)
class MonitoringReportSummary:
    run_id: UUID
    engine: str
    status: str
    progress: Decimal
    current_capital: Decimal
    profit_loss: Decimal
    generated_at: datetime


@dataclass(frozen=True)
class MonitoringAlert:
    level: str
    summary: str


@dataclass(frozen=True)
class MonitoringSummary:
    active_matches: int = 0
    pending_matches: int = 0
    recorded_activity: int = 0
    involved_capital: Decimal | None = None
    warning_count: int = 0
    error_count: int = 0


@dataclass(frozen=True)
class MonitoringCapitalCoverage:
    status: str
    amount: Decimal | None
    detail: str


@dataclass(frozen=True)
class MonitoringSnapshot:
    engines: tuple[MonitoringEngineStatus, ...]
    reports: tuple[MonitoringReportSummary, ...]
    latest_alert: MonitoringAlert | None
    history_available: bool
    summary: MonitoringSummary = field(default_factory=MonitoringSummary)
    capital_coverage: MonitoringCapitalCoverage = field(
        default_factory=lambda: MonitoringCapitalCoverage(
            status="gray",
            amount=None,
            detail="Live capital coverage is unavailable.",
        )
    )


@dataclass(frozen=True)
class ReportDetailLookup:
    report: SimulationReport | None
    message: str | None = None


class MonitoringService:
    """Build safe GUI read models from an optional, read-only report store."""

    def __init__(self, report_store: SimulationReportReader | None = None) -> None:
        self._report_store = report_store

    def snapshot(self) -> MonitoringSnapshot:
        reports, history_available = self._recent_reports()
        records_by_run = self._records_by_run(reports) if history_available else {}
        engines = self._engine_statuses(reports, records_by_run, history_available)
        summaries = tuple(self._summary_from_report(report) for report in reports)
        summary = MonitoringSummary(
            active_matches=sum(engine.running_matches for engine in engines),
            pending_matches=sum(engine.pending_matches for engine in engines),
            recorded_activity=sum(engine.total_activity for engine in engines),
            involved_capital=sum(
                (
                    engine.involved_capital
                    for engine in engines
                    if engine.involved_capital is not None
                ),
                Decimal(0),
            )
            if history_available
            else None,
            warning_count=sum(engine.warning_count for engine in engines),
            error_count=sum(engine.error_count for engine in engines),
        )
        return MonitoringSnapshot(
            engines=engines,
            reports=summaries,
            latest_alert=self._latest_alert(reports, records_by_run),
            history_available=history_available,
            summary=summary,
            capital_coverage=MonitoringCapitalCoverage(
                status="gray",
                amount=None,
                detail=(
                    "Recorded simulation capital is shown separately; live capital coverage is unavailable."
                    if history_available
                    else "Live capital coverage is unavailable because simulation history is not configured."
                ),
            ),
        )

    def load_report(
        self, run_id: UUID, selection: ReportDetailSelection
    ) -> ReportDetailLookup:
        if self._report_store is None:
            return ReportDetailLookup(
                report=None, message="Simulation history is not configured."
            )
        try:
            report = self._report_store.load_report(run_id)
        except KeyError:
            return ReportDetailLookup(report=None, message="This report is not available.")
        except (OSError, ValueError):
            return ReportDetailLookup(
                report=None, message="Simulation history is temporarily unavailable."
            )

        if not any(selection.model_dump().values()):
            return ReportDetailLookup(report=report)
        try:
            records = self._report_store.load_records(run_id)
        except KeyError:
            return ReportDetailLookup(
                report=report,
                message="Selected report details are not available for this run.",
            )
        except (OSError, ValueError):
            return ReportDetailLookup(
                report=report,
                message="Selected report details are temporarily unavailable.",
            )
        return ReportDetailLookup(
            report=SimulationReportBuilder().rebuild(report, records, selection)
        )

    def _recent_reports(self) -> tuple[tuple[SimulationReport, ...], bool]:
        if self._report_store is None:
            return (), False
        try:
            return self._report_store.list_recent_reports(limit=_REPORT_LIMIT), True
        except (OSError, ValueError):
            return (), False

    def _records_by_run(
        self, reports: tuple[SimulationReport, ...]
    ) -> dict[UUID, tuple[SimulationLogRecord, ...]]:
        if self._report_store is None:
            return {}
        records_by_run: dict[UUID, tuple[SimulationLogRecord, ...]] = {}
        for report in reports:
            try:
                records_by_run[report.run_id] = self._report_store.load_records(
                    report.run_id
                )
            except (KeyError, OSError, ValueError):
                continue
        return records_by_run

    @staticmethod
    def _summary_from_report(report: SimulationReport) -> MonitoringReportSummary:
        return MonitoringReportSummary(
            run_id=report.run_id,
            engine=str(report.engine),
            status=report.status.value,
            progress=report.progress,
            current_capital=report.current_capital,
            profit_loss=report.profit_loss,
            generated_at=report.generated_at,
        )

    @staticmethod
    def _engine_statuses(
        reports: tuple[SimulationReport, ...],
        records_by_run: dict[UUID, tuple[SimulationLogRecord, ...]],
        history_available: bool,
    ) -> tuple[MonitoringEngineStatus, ...]:
        statuses: list[MonitoringEngineStatus] = []
        for name, engine_id in _V1_ENGINES:
            engine_reports = tuple(
                report for report in reports if str(report.engine) == engine_id
            )
            latest = engine_reports[0] if engine_reports else None
            engine_records = tuple(
                record
                for report in engine_reports
                for record in records_by_run.get(report.run_id, ())
            )
            warning_count = sum(
                record.record_type is SimulationLogRecordType.WARNING
                for record in engine_records
            )
            error_count = sum(
                record.record_type is SimulationLogRecordType.ERROR
                for record in engine_records
            )
            status, detail = MonitoringService._status_for(
                latest, history_available=history_available
            )
            statuses.append(
                MonitoringEngineStatus(
                    name=name,
                    status=status,
                    detail=detail,
                    engine_id=engine_id,
                    active=latest is not None and latest.status is SimulationStatus.RUNNING,
                    mode="simulation" if latest is not None else "unavailable",
                    live_state="unavailable",
                    running_matches=sum(
                        report.status is SimulationStatus.RUNNING
                        for report in engine_reports
                    ),
                    pending_matches=sum(
                        report.status is SimulationStatus.PENDING
                        for report in engine_reports
                    ),
                    total_activity=len(engine_reports),
                    involved_capital=(
                        latest.current_capital if latest is not None else None
                    ),
                    warning_count=warning_count,
                    error_count=error_count,
                    latest_report_id=latest.run_id if latest is not None else None,
                    workflow_stages=MonitoringService._workflow_stages(engine_records),
                )
            )
        return tuple(statuses)

    @staticmethod
    def _status_for(
        report: SimulationReport | None, *, history_available: bool
    ) -> tuple[str, str]:
        if not history_available:
            return "gray", "Simulation history is not configured."
        if report is None:
            return "gray", "No simulation activity recorded."
        if report.status is SimulationStatus.RUNNING:
            return "green", "Simulation running."
        if report.status in (SimulationStatus.PENDING, SimulationStatus.STOPPED):
            return "amber", f"Latest simulation {report.status.value}."
        return "gray", "Latest simulation completed."

    @staticmethod
    def _workflow_stages(
        records: tuple[SimulationLogRecord, ...]
    ) -> tuple[MonitoringWorkflowStage, ...]:
        transitions = {
            str(record.payload.get("stage")): record
            for record in records
            if record.record_type is SimulationLogRecordType.WORKFLOW_TRANSITION
        }
        stages: list[MonitoringWorkflowStage] = []
        for stage, name in _WORKFLOW_STAGES:
            record = transitions.get(stage.value)
            if record is None:
                stages.append(
                    MonitoringWorkflowStage(
                        name=name,
                        status="gray",
                        detail="No recorded workflow transition.",
                    )
                )
                continue
            decision = str(record.payload.get("decision", "allow"))
            status = {
                WorkflowDecision.ALLOW.value: "green",
                WorkflowDecision.RECHECK.value: "amber",
                WorkflowDecision.REJECT.value: "red",
            }.get(decision, "gray")
            detail = decision
            if reason := record.payload.get("reason"):
                detail = f"{decision}: {reason}"
            stages.append(MonitoringWorkflowStage(name=name, status=status, detail=detail))
        return tuple(stages)

    @staticmethod
    def _latest_alert(
        reports: tuple[SimulationReport, ...],
        records_by_run: dict[UUID, tuple[SimulationLogRecord, ...]],
    ) -> MonitoringAlert | None:
        for report in reports:
            for record in reversed(records_by_run.get(report.run_id, ())):
                if record.record_type is SimulationLogRecordType.ERROR:
                    return MonitoringAlert("red", "Latest simulation recorded an error.")
                if record.record_type is SimulationLogRecordType.WARNING:
                    return MonitoringAlert("amber", "Latest simulation recorded a warning.")
        return None