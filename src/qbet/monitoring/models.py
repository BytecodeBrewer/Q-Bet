"""Typed, redacted records used exclusively by the administrator monitoring plane."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator

from qbet.domain.models import DomainModel, Identifier

_SENSITIVE_MARKERS = ("credential", "password", "secret", "token", "api_key", "session")


class MonitoringLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class MonitoringRecord(DomainModel):
    """One safe, material pipeline observation tied to a correlation identifier."""

    correlation_id: UUID
    occurred_at: AwareDatetime
    engine: Identifier
    mode: Identifier | None = None
    stage: Identifier
    event_type: Identifier
    status: Identifier
    reason_code: Identifier | None = None
    level: MonitoringLevel = MonitoringLevel.INFO
    duration_ms: int | None = Field(default=None, ge=0)
    references: dict[str, Any] = Field(default_factory=dict)

    @field_validator("references")
    @classmethod
    def redacts_sensitive_references(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _redact(value)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if any(marker in key.lower() for marker in _SENSITIVE_MARKERS)
            else _redact(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value
