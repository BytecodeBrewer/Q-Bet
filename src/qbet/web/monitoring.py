"""Read-only GUI monitoring models for execution and simulation planes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from collections.abc import Mapping
from typing import Final, Literal, cast
from uuid import UUID

from qbet.layers.logging import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import (
    ReportDetailSelection,
    SimulationReport,
    SimulationReportBuilder,
)
from qbet.simulation import SimulationStatus
from qbet.storage import SimulationReportReader
from qbet.workflow import WorkflowDecision, WorkflowMode, WorkflowStage
from qbet.workflow.readiness import Phase2PipelineReadiness
from qbet.workflow.routing import RoutingConfiguration, V1Engine, engine_modes

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
class EngineNotice:
    severity: Literal["info", "success", "warning", "error"]
    reason_code: str
    title: str
    detail: str
    action_label: str | None = None
    action_url: str | None = None
    dismissible: bool = True
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class MonitoringEngineStatus:
    name: str
    status: str
    detail: str
    engine_id: str = ""
    active: bool = False
    enabled: bool = False
    running: bool = False
    mode: str = "unavailable"
    live_state: str = "unavailable"
    running_matches: int = 0
    pending_matches: int = 0
    total_activity: int = 0
    involved_capital: Decimal | None = None
    warning_count: int = 0
    error_count: int = 0
    latest_report_id: UUID | None = None
    latest_report_generated_at: datetime | None = None
    latest_no_opportunity: bool = False
    notices: tuple[EngineNotice, ...] = ()
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
    active_engines: int = 0
    running_engines: int = 0


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


def _runtime_stages(
    engine_id: str,
    mode: WorkflowMode,
    *,
    active: bool,
    available: bool,
) -> tuple[MonitoringWorkflowStage, ...]:
    names = {stage: name for stage, name in _WORKFLOW_STAGES}
    if not available:
        return tuple(
            MonitoringWorkflowStage(name=name, status="red", detail="Unavailable")
            for _, name in _WORKFLOW_STAGES
        )
    if not active:
        return tuple(
            MonitoringWorkflowStage(name=name, status="gray", detail="Inactive")
            for _, name in _WORKFLOW_STAGES
        )
    readiness = Phase2PipelineReadiness().snapshot(cast(V1Engine, engine_id), mode)
    return tuple(
        MonitoringWorkflowStage(
            name=names[item.stage],
            status="green" if item.ready else "red",
            detail="Ready" if item.ready else (item.reason or "Unavailable"),
        )
        for item in readiness
    )


def execution_snapshot(
    configuration: RoutingConfiguration | None = None,
    *,
    configuration_available: bool = True,
    runtime_activity: Mapping[str, tuple[int, int]] | None = None,
) -> MonitoringSnapshot:
    """Return execution readiness separately from actual queued/running work."""

    activity = runtime_activity or {}
    engines: list[MonitoringEngineStatus] = []
    for name, engine_id in _V1_ENGINES:
        running_matches, pending_matches = activity.get(engine_id, (0, 0))
        if configuration is None:
            enabled = False
            active = False
            running = False
            status = "gray"
            detail = "No live execution activity is connected yet."
            live_state = "unavailable"
            stages = tuple(
                MonitoringWorkflowStage(
                    name=stage_name,
                    status="gray",
                    detail="No live execution state is connected.",
                )
                for _, stage_name in _WORKFLOW_STAGES
            )
        else:
            enabled = configuration_available and engine_modes(
                configuration, cast(V1Engine, engine_id)
            ).execution
            active = enabled
            running = enabled and running_matches > 0
            status = (
                "red"
                if not configuration_available
                else "green"
                if running
                else "gray"
            )
            detail = (
                "Control unavailable."
                if not configuration_available
                else "Running."
                if running
                else "Ready; no work running."
                if active
                else "Inactive."
            )
            live_state = (
                "error"
                if not configuration_available
                else "running"
                if running
                else "ready"
                if active
                else "inactive"
            )
            stages = _runtime_stages(
                engine_id,
                WorkflowMode.EXECUTION,
                active=enabled,
                available=configuration_available,
            )
        engines.append(
            MonitoringEngineStatus(
                name=name,
                engine_id=engine_id,
                status=status,
                detail=detail,
                active=active,
                enabled=enabled,
                running=running,
                mode="execution" if configuration is not None else "live",
                live_state=live_state,
                running_matches=running_matches,
                pending_matches=pending_matches,
                workflow_stages=stages,
            )
        )
    return MonitoringSnapshot(
        engines=tuple(engines),
        reports=(),
        latest_alert=None,
        history_available=False,
        summary=MonitoringSummary(
            active_matches=sum(engine.running_matches for engine in engines),
            pending_matches=sum(engine.pending_matches for engine in engines),
            active_engines=sum(engine.active for engine in engines),
            running_engines=sum(engine.running for engine in engines),
        ),
        capital_coverage=MonitoringCapitalCoverage(
            status="gray",
            amount=None,
            detail="Live capital coverage is unavailable.",
        ),
    )


class MonitoringService:
    """Build simulation read models from an optional, read-only report store."""

    def __init__(self, report_store: SimulationReportReader | None = None) -> None:
        self._report_store = report_store

    def snapshot(
        self,
        *,
        runtime_configuration: RoutingConfiguration | None = None,
        runtime_available: bool = True,
        runtime_activity: Mapping[str, tuple[int, int]] | None = None,
    ) -> MonitoringSnapshot:
        reports, history_available = self._recent_reports()
        records_by_run = self._records_by_run(reports) if history_available else {}
        engines = self._engine_statuses(
            reports,
            records_by_run,
            history_available,
            runtime_configuration=runtime_configuration,
            runtime_available=runtime_available,
            runtime_activity=runtime_activity,
        )
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
            active_engines=sum(engine.active for engine in engines),
            running_engines=sum(engine.running for engine in engines),
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
                    "Recorded simulation capital is shown in the Simulation plane; live capital coverage is unavailable."
                    if history_available
                    else "Simulation history is not configured."
                ),
            ),
        )

    def load_report(self, run_id: UUID, selection: ReportDetailSelection) -> ReportDetailLookup:
        if self._report_store is None:
            return ReportDetailLookup(report=None, message="Simulation history is not configured.")
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
                records_by_run[report.run_id] = self._report_store.load_records(report.run_id)
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
        *,
        runtime_configuration: RoutingConfiguration | None,
        runtime_available: bool,
        runtime_activity: Mapping[str, tuple[int, int]] | None,
    ) -> tuple[MonitoringEngineStatus, ...]:
        statuses: list[MonitoringEngineStatus] = []
        activity = runtime_activity or {}
        for name, engine_id in _V1_ENGINES:
            engine_reports = tuple(report for report in reports if str(report.engine) == engine_id)
            latest = engine_reports[0] if engine_reports else None
            engine_records = tuple(
                record
                for report in engine_reports
                for record in records_by_run.get(report.run_id, ())
            )
            latest_records = (
                records_by_run.get(latest.run_id, ()) if latest is not None else ()
            )
            current_records = (
                latest_records
                if latest is not None and latest.status is SimulationStatus.RUNNING
                else ()
            )
            warning_count = sum(
                record.record_type is SimulationLogRecordType.WARNING for record in engine_records
            )
            error_count = sum(
                record.record_type is SimulationLogRecordType.ERROR for record in engine_records
            )
            current_warning_count = sum(
                record.record_type is SimulationLogRecordType.WARNING for record in current_records
            )
            current_error_count = sum(
                record.record_type is SimulationLogRecordType.ERROR for record in current_records
            )
            latest_profitability = tuple(
                bool(record.payload["is_profitable"])
                for record in latest_records
                if record.record_type is SimulationLogRecordType.EVALUATION
                and isinstance(record.payload.get("is_profitable"), bool)
            )
            latest_no_opportunity = bool(
                latest is not None
                and latest.status is SimulationStatus.COMPLETED
                and latest_profitability
                and not any(latest_profitability)
            )

            running_matches, pending_matches = activity.get(engine_id, (0, 0))
            if runtime_configuration is None:
                enabled = False
                status, detail = MonitoringService._status_for(
                    latest, history_available=history_available
                )
                running = latest is not None and latest.status is SimulationStatus.RUNNING
                active = running
                live_state = "running" if running else "unavailable"
                workflow_stages = MonitoringService._workflow_stages(engine_records)
            else:
                enabled = runtime_available and engine_modes(
                    runtime_configuration, cast(V1Engine, engine_id)
                ).simulation
                active = enabled
                running = enabled and running_matches > 0
                if not runtime_available:
                    status, detail, live_state = "red", "Control unavailable.", "error"
                elif not active:
                    status, detail, live_state = "gray", "Inactive.", "inactive"
                elif running and current_error_count:
                    status, detail, live_state = "red", "Current run error.", "error"
                elif running and current_warning_count:
                    status, detail, live_state = "amber", "Current run warning.", "warning"
                elif running:
                    status, detail, live_state = "green", "Running.", "running"
                else:
                    status, detail, live_state = "gray", "Ready; no work running.", "ready"
                workflow_stages = MonitoringService._workflow_stages(
                    current_records,
                    engine_id=engine_id,
                    runtime_active=enabled,
                    runtime_available=runtime_available,
                )

            statuses.append(
                MonitoringEngineStatus(
                    name=name,
                    status=status,
                    detail=detail,
                    engine_id=engine_id,
                    active=active,
                    enabled=enabled,
                    running=running,
                    mode="simulation",
                    live_state=live_state,
                    running_matches=running_matches
                    if runtime_configuration is not None
                    else sum(
                        report.status is SimulationStatus.RUNNING for report in engine_reports
                    ),
                    pending_matches=pending_matches
                    if runtime_configuration is not None
                    else sum(
                        report.status is SimulationStatus.PENDING for report in engine_reports
                    ),
                    total_activity=len(engine_reports),
                    involved_capital=(latest.current_capital if latest is not None else None),
                    warning_count=warning_count,
                    error_count=error_count,
                    latest_report_id=latest.run_id if latest is not None else None,
                    latest_report_generated_at=latest.generated_at if latest is not None else None,
                    latest_no_opportunity=latest_no_opportunity,
                    workflow_stages=workflow_stages,
                )
            )
        return tuple(statuses)

    @staticmethod
    def _status_for(report: SimulationReport | None, *, history_available: bool) -> tuple[str, str]:
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
        records: tuple[SimulationLogRecord, ...],
        *,
        engine_id: str | None = None,
        runtime_active: bool | None = None,
        runtime_available: bool = True,
    ) -> tuple[MonitoringWorkflowStage, ...]:
        if runtime_active is not None and engine_id is not None:
            base = _runtime_stages(
                engine_id,
                WorkflowMode.SIMULATION,
                active=runtime_active,
                available=runtime_available,
            )
            if not runtime_available or not runtime_active:
                return base
        else:
            base = ()

        transitions = {
            str(record.payload.get("stage")): record
            for record in records
            if record.record_type is SimulationLogRecordType.WORKFLOW_TRANSITION
        }
        stages: list[MonitoringWorkflowStage] = []
        base_by_name = {stage.name: stage for stage in base}
        for stage, name in _WORKFLOW_STAGES:
            record = transitions.get(stage.value)
            if record is None:
                if name in base_by_name:
                    stages.append(base_by_name[name])
                else:
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
