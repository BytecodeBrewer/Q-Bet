"""Replaceable persistence contracts for simulation reports."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from qbet.layers.logging import SimulationLogRecord
from qbet.reporting import SimulationReport


class SimulationReportStore(Protocol):
    def append_records(self, records: tuple[SimulationLogRecord, ...]) -> None: ...

    def finalize_run(self, report: SimulationReport) -> None: ...

    def load_report(self, run_id: UUID) -> SimulationReport: ...

    def load_records(self, run_id: UUID) -> tuple[SimulationLogRecord, ...]: ...

    def list_recent_reports(self, limit: int = 20) -> tuple[SimulationReport, ...]: ...
