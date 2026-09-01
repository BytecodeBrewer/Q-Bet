"""Read-only presentation summaries for persisted simulation reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from qbet.layers.logging import SimulationLogRecordType
from qbet.reporting import SimulationReport
from qbet.simulation import SimulationStatus
from qbet.storage import SimulationReportStore

_REPORT_LIMIT: Final = 10
_V1_ENGINES: Final = (
    ("BonusEngine", "bonus"),
    ("SportsCapitalEngine", "sports_capital"),
)


@dataclass(frozen=True)
class MonitoringEngineStatus:
    name: str
    status: str
    detail: str


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
class MonitoringSnapshot:
    engines: tuple[MonitoringEngineStatus, ...]
    reports: tuple[MonitoringReportSummary, ...]
    latest_alert: MonitoringAlert | None
    history_available: bool


class MonitoringService:
    """Build safe, read-only monitoring data from an optional report store."""

    def __init__(self, report_store: SimulationReportStore | None = None) -> None:
        self._report_store = report_store

    def snapshot(self) -> MonitoringSnapshot:
        if self._report_store is None:
            return MonitoringSnapshot(
                engines=self._engine_statuses((), history_available=False),
                reports=(),
                latest_alert=None,
                history_available=False,
            )

        try:
            reports = self._report_store.list_recent_reports(limit=_REPORT_LIMIT)
        except (OSError, ValueError):
            return MonitoringSnapshot(
                engines=self._engine_statuses((), history_available=False),
                reports=(),
                latest_alert=None,
                history_available=False,
            )

        return MonitoringSnapshot(
            engines=self._engine_statuses(reports, history_available=True),
            reports=tuple(
                MonitoringReportSummary(
                    run_id=report.run_id,
                    engine=report.engine,
                    status=report.status,
                    progress=report.progress,
                    current_capital=report.current_capital,
                    profit_loss=report.profit_loss,
                    generated_at=report.generated_at,
                )
                for report in reports
            ),
            latest_alert=self._latest_alert(reports),
            history_available=True,
        )

    @staticmethod
    def _engine_statuses(
        reports: tuple[SimulationReport, ...], *, history_available: bool
    ) -> tuple[MonitoringEngineStatus, ...]:
        latest_reports = {report.engine: report for report in reports}
        return tuple(
            MonitoringEngineStatus(
                name=name,
                status=MonitoringService._status_for(
                    latest_reports.get(engine_id), history_available=history_available
                )[0],
                detail=MonitoringService._status_for(
                    latest_reports.get(engine_id), history_available=history_available
                )[1],
            )
            for name, engine_id in _V1_ENGINES
        )

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

    def _latest_alert(
        self, reports: tuple[SimulationReport, ...]
    ) -> MonitoringAlert | None:
        if self._report_store is None:
            return None

        for report in reports:
            try:
                records = self._report_store.load_records(report.run_id)
            except KeyError:
                continue
            except OSError:
                return MonitoringAlert("amber", "Simulation history is unavailable.")

            for record in reversed(records):
                if record.record_type is SimulationLogRecordType.ERROR:
                    return MonitoringAlert("red", "Latest simulation recorded an error.")
                if record.record_type is SimulationLogRecordType.WARNING:
                    return MonitoringAlert("amber", "Latest simulation recorded a warning.")
        return None
