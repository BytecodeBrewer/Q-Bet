"""Bounded, customer-safe aggregation over durable completed result reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from qbet.reporting.customer import CustomerReportUnavailable, CustomerResultReport
from qbet.reporting.models import SimulationReport

MAX_REPORTING_RANGE = timedelta(days=31)
MAX_REPORTS = 500


class CustomerReportReader(Protocol):
    def list_recent_reports(self, limit: int = 20) -> tuple[SimulationReport, ...]: ...


@dataclass(frozen=True)
class CustomerReportingQuery:
    start: datetime
    end: datetime
    engine: str | None = None
    mode: str | None = None

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("reporting end must not precede start")
        if self.end - self.start > MAX_REPORTING_RANGE:
            raise ValueError("reporting range must not exceed 31 days")
        if self.mode not in {None, "", "simulation"}:
            raise ValueError("unsupported reporting mode")
        if self.engine not in {None, "", "bonus", "sports_capital"}:
            raise ValueError("unsupported reporting engine")


@dataclass(frozen=True)
class CustomerReportingDashboard:
    reports: tuple[CustomerResultReport, ...]
    completed_count: int
    invested_capital: Decimal
    profit_loss: Decimal
    roi: Decimal | None
    available: bool = True


class CustomerReportingService:
    """Read-only business projection; unavailable or incomplete records stay excluded."""

    def __init__(self, reader: CustomerReportReader) -> None:
        self._reader = reader

    def dashboard(self, query: CustomerReportingQuery) -> CustomerReportingDashboard:
        try:
            source = self._reader.list_recent_reports(limit=MAX_REPORTS)
        except OSError:
            return CustomerReportingDashboard((), 0, Decimal("0"), Decimal("0"), None, False)

        reports: list[CustomerResultReport] = []
        for source_report in source:
            try:
                report = CustomerResultReport.from_simulation_report(source_report)
            except CustomerReportUnavailable:
                continue
            if not query.start <= report.completed_at <= query.end:
                continue
            if query.engine and report.engine != query.engine:
                continue
            if query.mode and report.mode != query.mode:
                continue
            reports.append(report)
        reports.sort(key=lambda report: report.completed_at, reverse=True)
        capital = sum((report.invested_capital for report in reports), Decimal("0"))
        profit = sum((report.profit_loss for report in reports), Decimal("0"))
        return CustomerReportingDashboard(
            tuple(reports),
            len(reports),
            capital,
            profit,
            (profit / capital * Decimal("100")) if capital else None,
        )
