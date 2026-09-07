"""Stable administrator-only Monitoring export projections."""

from __future__ import annotations

import json
from collections.abc import Iterable

from qbet.monitoring.models import MonitoringRecord
from qbet.monitoring.service import CompactMonitoringProcess


def json_document(values: Iterable[MonitoringRecord | CompactMonitoringProcess]) -> bytes:
    return json.dumps(
        [value.model_dump(mode="json") for value in values], indent=2, sort_keys=True
    ).encode("utf-8")


def csv_rows(values: Iterable[MonitoringRecord | CompactMonitoringProcess]) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    for value in values:
        payload = value.model_dump(mode="json")
        rows.append(tuple(str(payload.get(field, "")) for field in _fields(value)))
    return tuple(rows)


def csv_header(*, extended: bool) -> tuple[str, ...]:
    return tuple(
        (MonitoringRecord if extended else CompactMonitoringProcess).model_fields
    )


def _fields(value: MonitoringRecord | CompactMonitoringProcess) -> tuple[str, ...]:
    return tuple(type(value).model_fields)
