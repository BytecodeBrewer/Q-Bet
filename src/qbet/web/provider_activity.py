"""Customer-safe provider/data-ingestion activity projection for the web UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from qbet.monitoring import MonitoringRecord

_WORKING_STALE_AFTER = timedelta(seconds=30)


@dataclass(frozen=True)
class ProviderActivitySnapshot:
    state: str
    label: str
    occurred_at: datetime | None = None
    provider: str | None = None
    duration_ms: int | None = None
    reason_code: str | None = None

    @property
    def is_working(self) -> bool:
        return self.state == "working"


def provider_activity_snapshot(
    records: Iterable[MonitoringRecord],
    *,
    now: datetime,
    available: bool = True,
) -> ProviderActivitySnapshot:
    """Project recent provider observations without exposing technical payloads."""

    if not available:
        return ProviderActivitySnapshot(
            state="unavailable",
            label="Market data source unavailable.",
        )

    relevant = tuple(
        record
        for record in records
        if record.event_type in {"provider_query", "polling"}
        or record.stage == "request_handler"
    )
    if not relevant:
        return ProviderActivitySnapshot(
            state="ready",
            label="Market data ready; no query running.",
        )

    latest = max(relevant, key=lambda record: record.occurred_at)
    state = _normalized_state(str(latest.status), latest.reason_code)
    if state == "working" and now - latest.occurred_at > _WORKING_STALE_AFTER:
        state = "delayed"

    provider = latest.references.get("provider_id") or latest.references.get("source_id")
    return ProviderActivitySnapshot(
        state=state,
        label=_label(state),
        occurred_at=latest.occurred_at,
        provider=str(provider) if provider is not None else None,
        duration_ms=latest.duration_ms,
        reason_code=str(latest.reason_code) if latest.reason_code is not None else None,
    )


def _normalized_state(status: str, reason_code: object) -> str:
    reason = str(reason_code or "").lower()
    if "delay" in reason or "rate_limit" in reason or "rate_limited" in reason:
        return "delayed"
    if status in {"working", "running", "processing", "started", "pending", "fetching"}:
        return "working"
    if status in {"success", "completed", "ready", "allow", "skipped_fresh"}:
        return "success"
    if status in {"delayed", "recheck"}:
        return "delayed"
    if status == "scheduled":
        return "ready"
    if status in {"unavailable", "not_ready"}:
        return "unavailable"
    if status in {"terminal", "disabled"}:
        return "ready"
    if status in {
        "error",
        "failed",
        "reject",
        "rejected",
        "provider_error",
        "configuration_error",
    }:
        return "error"
    return "ready"


def _label(state: str) -> str:
    return {
        "ready": "Market data ready; no query running.",
        "working": "Market data is updating.",
        "success": "Market data updated successfully.",
        "delayed": "Market data update is delayed.",
        "unavailable": "Market data source unavailable.",
        "error": "Market data update failed.",
    }[state]
