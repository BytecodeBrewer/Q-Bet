"""PostgreSQL persistence for administrator-only monitoring events."""

from __future__ import annotations

from django.db import DatabaseError

from qbet.monitoring.models import MonitoringRecord
from qbet.monitoring.service import MonitoringQuery
from qbet.storage.models import MonitoringRecordRow


class PostgresMonitoringRepository:
    def append(self, record: MonitoringRecord) -> MonitoringRecord:
        try:
            MonitoringRecordRow.objects.create(
                correlation_id=record.correlation_id,
                occurred_at=record.occurred_at,
                payload=record.model_dump(mode="json"),
            )
        except DatabaseError as error:
            raise OSError("monitoring persistence is unavailable") from error
        return record

    def list_records(self, query: MonitoringQuery) -> tuple[MonitoringRecord, ...]:
        try:
            rows = MonitoringRecordRow.objects.filter(
                occurred_at__gte=query.start,
                occurred_at__lte=query.end,
            )
            if query.correlation_id is not None:
                rows = rows.filter(correlation_id=query.correlation_id)
            payloads = tuple(rows.order_by("occurred_at", "id").values_list("payload", flat=True)[: query.limit])
        except DatabaseError as error:
            raise OSError("monitoring history is unavailable") from error
        return tuple(MonitoringRecord.model_validate(payload) for payload in payloads)
