"""Replaceable persistence contracts for reports, provider state, and analytics replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from qbet.domain.models import Identifier
from qbet.domain.verification import ProviderState
from qbet.layers.logging import SimulationLogRecord
from qbet.reporting import SimulationReport


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    """Ordered analytical payload used by replay/backtesting storage."""

    stream_id: str
    sequence: int
    payload: str

    def __post_init__(self) -> None:
        if not self.stream_id.strip():
            raise ValueError("stream_id must not be empty")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")


class ReplayStore(Protocol):
    def append(self, record: ReplayRecord) -> None: ...

    def load_stream(self, stream_id: str) -> tuple[ReplayRecord, ...]: ...


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
