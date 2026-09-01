"""Read-only presentation summaries for persisted simulation reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final
from uuid import UUID

from qbet.layers.logging import SimulationLogRecordType
from qbet.reporting import SimulationReport
from qbet.storage import SimulationReportStore

_REPORT_LIMIT: Final = 10


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
    reports: tuple[MonitoringReportSummary, ...]
    latest_alert: MonitoringAlert | None
    history_available: bool


class MonitoringService:
    """Build safe, read-only monitoring data from an optional report store."""

    def __init__(self, report_store: SimulationReportStore | None = None) -> None:
        self._report_store = report_store

    def snapshot(self) -> MonitoringSnapshot:
        if self._report_store is None:
            return MonitoringSnapshot((), None, False)

        try:
            reports = self._report_store.list_recent_reports(limit=_REPORT_LIMIT)
        except (OSError, ValueError):
            return MonitoringSnapshot((), None, False)

        return MonitoringSnapshot(
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