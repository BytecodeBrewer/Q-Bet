"""Replaceable persistence contracts for reports and provider state."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from qbet.domain.models import Identifier
from qbet.domain.verification import ProviderState
from qbet.layers.logging import SimulationLogRecord
from qbet.reporting import SimulationReport


class SimulationReportReader(Protocol):
    """Read-only report history needed by monitoring and report views."""

    def load_records(self, run_id: UUID) -> tuple[SimulationLogRecord, ...]: ...

    def load_report(self, run_id: UUID) -> SimulationReport: ...

    def list_recent_reports(self, limit: int = 20) -> tuple[SimulationReport, ...]: ...


class SimulationReportStore(SimulationReportReader, Protocol):
    def append_records(self, records: tuple[SimulationLogRecord, ...]) -> None: ...

    def finalize_run(self, report: SimulationReport) -> None: ...

    def load_report(self, run_id: UUID) -> SimulationReport: ...


class ProviderStateRepository(Protocol):
    """Replaceable local state seam for a future Supabase/PostgreSQL adapter."""

    def get(self, provider_id: Identifier) -> ProviderState | None: ...

    def upsert(self, state: ProviderState) -> None: ...
