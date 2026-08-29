"""Structured, observational records for Q-Bet simulation runs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from qbet.domain.models import DomainModel, Identifier


SCHEMA_VERSION = 1
_SENSITIVE_FIELD_MARKERS = ("credential", "password", "secret", "token", "api_key")


class SimulationLogRecordType(StrEnum):
    RUN_STARTED = "run_started"
    EVENT = "event"
    EVALUATION = "evaluation"
    CAPITAL_TRANSITION = "capital_transition"
    INTERMEDIATE_RESULT = "intermediate_result"
    RAW_INPUT = "raw_input"
    WARNING = "warning"
    ERROR = "error"
    RUN_FINISHED = "run_finished"


class SimulationLogRecord(DomainModel):
    run_id: UUID
    sequence: int = Field(gt=0)
    timestamp: datetime
    record_type: SimulationLogRecordType
    source: Identifier
    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include timezone information")
        return value


class SimulationLogContext:
    """Collects ordered records without changing the observed simulation."""

    def __init__(
        self,
        run_id: UUID | None = None,
        on_record: Callable[[SimulationLogRecord], None] | None = None,
    ) -> None:
        self.run_id = run_id or uuid4()
        self._on_record = on_record
        self._records: list[SimulationLogRecord] = []

    @property
    def records(self) -> tuple[SimulationLogRecord, ...]:
        return tuple(self._records)

    def record(
        self,
        record_type: SimulationLogRecordType,
        source: str,
        payload: dict[str, Any] | None = None,
    ) -> SimulationLogRecord:
        record = SimulationLogRecord(
            run_id=self.run_id,
            sequence=len(self._records) + 1,
            timestamp=datetime.now(timezone.utc),
            record_type=record_type,
            source=source,
            payload=_redact_sensitive_values(payload or {}),
        )
        self._records.append(record)
        if self._on_record is not None:
            self._on_record(record)
        return record


def _redact_sensitive_values(value: Any) -> Any:
    """Prevent accidental credentials from entering the observational log."""

    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if any(marker in key.lower() for marker in _SENSITIVE_FIELD_MARKERS)
            else _redact_sensitive_values(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_sensitive_values(item) for item in value)
    return value
