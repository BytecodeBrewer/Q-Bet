"""Read projections over one persisted monitoring event source."""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, model_validator

from qbet.domain.models import DomainModel
from qbet.monitoring.models import MonitoringRecord

MAX_EXPORT_RANGE = timedelta(days=31)


class MonitoringReader(Protocol):
    def list_records(self, query: "MonitoringQuery") -> tuple[MonitoringRecord, ...]: ...


class MonitoringQuery(DomainModel):
    start: AwareDatetime
    end: AwareDatetime
    correlation_id: UUID | None = None

    @model_validator(mode="after")
    def validates_range(self) -> "MonitoringQuery":
        if self.end < self.start:
            raise ValueError("end must not be before start")
        if self.end - self.start > MAX_EXPORT_RANGE:
            raise ValueError("monitoring range must not exceed 31 days")
        return self


class CompactMonitoringProcess(DomainModel):
    correlation_id: UUID
    work_id: str | None = None
    engine: str
    mode: str | None = None
    status: str
    started_at: AwareDatetime
    finished_at: AwareDatetime
    duration_ms: int | None = None
    opportunity_id: str | None = None
    warning_count: int = 0
    error_count: int = 0
    latest_reason_code: str | None = None


class MonitoringService:
    def __init__(self, reader: MonitoringReader) -> None:
        self._reader = reader

    def extended(self, query: MonitoringQuery) -> tuple[MonitoringRecord, ...]:
        return self._reader.list_records(query)

    def compact(self, query: MonitoringQuery) -> tuple[CompactMonitoringProcess, ...]:
        by_correlation_mode: dict[tuple[UUID, str | None], list[MonitoringRecord]] = {}
        for record in self.extended(query):
            mode = str(record.mode) if record.mode is not None else None
            by_correlation_mode.setdefault((record.correlation_id, mode), []).append(record)

        grouped: list[tuple[tuple[UUID, str | None, str | None], list[MonitoringRecord]]] = []
        for (correlation_id, mode), records in by_correlation_mode.items():
            work_ids = {
                str(record.references["work_id"])
                for record in records
                if record.references.get("work_id") is not None
            }
            if len(work_ids) <= 1:
                work_id = next(iter(work_ids), None)
                grouped.append(((correlation_id, mode, work_id), records))
                continue

            by_work: dict[str | None, list[MonitoringRecord]] = {}
            for record in records:
                raw_work_id = record.references.get("work_id")
                work_id = str(raw_work_id) if raw_work_id is not None else None
                by_work.setdefault(work_id, []).append(record)
            grouped.extend(
                ((correlation_id, mode, work_id), work_records)
                for work_id, work_records in by_work.items()
            )

        grouped.sort(
            key=lambda item: (
                item[0][0].hex,
                item[0][1] or "",
                item[0][2] or "",
            )
        )
        return tuple(self._compact(records) for _, records in grouped)

    @staticmethod
    def _compact(records: list[MonitoringRecord]) -> CompactMonitoringProcess:
        ordered = sorted(records, key=lambda record: record.occurred_at)
        latest = ordered[-1]
        opportunity_id = next(
            (
                str(record.references["opportunity_id"])
                for record in reversed(ordered)
                if record.references.get("opportunity_id") is not None
            ),
            None,
        )
        work_id = next(
            (
                str(record.references["work_id"])
                for record in reversed(ordered)
                if record.references.get("work_id") is not None
            ),
            None,
        )
        latest_reason_code = next(
            (
                str(record.reason_code)
                for record in reversed(ordered)
                if record.reason_code is not None
            ),
            None,
        )
        return CompactMonitoringProcess(
            correlation_id=latest.correlation_id,
            work_id=work_id,
            engine=str(latest.engine),
            mode=str(latest.mode) if latest.mode is not None else None,
            status=str(latest.status),
            started_at=ordered[0].occurred_at,
            finished_at=latest.occurred_at,
            duration_ms=latest.duration_ms
            if latest.duration_ms is not None
            else int((latest.occurred_at - ordered[0].occurred_at).total_seconds() * 1000),
            opportunity_id=opportunity_id,
            warning_count=sum(record.level.value == "warning" for record in ordered),
            error_count=sum(record.level.value == "error" for record in ordered),
            latest_reason_code=latest_reason_code,
        )
