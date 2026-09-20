"""PostgreSQL-backed aggregate observability projection."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from django.db import DatabaseError
from django.db.models import Count

from qbet.observability.metrics import (
    ObservabilitySnapshot,
    aggregate_monitoring,
    metric_dimensions,
)
from qbet.storage.models import ExecutionRecordRow, ModeWorkQueueRow, MonitoringRecordRow


class ObservabilityPersistenceError(OSError):
    """Raised when durable observability data cannot be projected."""


class PostgresObservabilityRepository:
    """Read-only aggregate projection over existing durable control-plane state."""

    def snapshot(self, *, start: datetime, end: datetime) -> ObservabilitySnapshot:
        """Project a bounded Monitoring window plus current queue/execution state."""

        return self._snapshot(start=start, end=end)

    def cumulative_snapshot(self, *, end: datetime) -> ObservabilitySnapshot:
        """Project append-only Monitoring history for monotone Prometheus series."""

        return self._snapshot(start=None, end=end)

    def _snapshot(
        self,
        *,
        start: datetime | None,
        end: datetime,
    ) -> ObservabilitySnapshot:
        try:
            monitoring_query = MonitoringRecordRow.objects.filter(occurred_at__lte=end)
            if start is not None:
                monitoring_query = monitoring_query.filter(occurred_at__gte=start)
            monitoring_rows = tuple(
                monitoring_query.values_list(
                    "payload__engine",
                    "payload__mode",
                    "payload__level",
                    "payload__duration_ms",
                    "payload__status",
                    "occurred_at",
                )
            )
            events, durations = aggregate_monitoring(
                tuple(
                    (engine, mode, level, duration)
                    for engine, mode, level, duration, _, _ in monitoring_rows
                )
            )
            failures: defaultdict[tuple[str, str, str], int] = defaultdict(int)
            for engine, mode, _, _, status, _ in monitoring_rows:
                if status not in {"failed", "recheck"}:
                    continue
                metric_engine, metric_mode = metric_dimensions(engine, mode)
                outcome = "retry" if status == "recheck" else "failure"
                failures[(metric_engine, metric_mode, outcome)] += 1
            latest_timestamp = max((row[-1] for row in monitoring_rows), default=None)
            queue_items = {
                (row["mode"], row["state"]): row["count"]
                for row in ModeWorkQueueRow.objects.values("mode", "state").annotate(
                    count=Count("work_id")
                )
            }
            execution_records = {
                (row["mode"], row["state"]): row["count"]
                for row in ExecutionRecordRow.objects.values("mode", "state").annotate(
                    count=Count("record_id")
                )
            }
        except DatabaseError as error:
            raise ObservabilityPersistenceError(
                "durable observability data is unavailable"
            ) from error
        return ObservabilitySnapshot(
            True,
            events,
            durations,
            queue_items,
            execution_records,
            int(latest_timestamp.timestamp()) if latest_timestamp is not None else None,
            dict(failures),
        )
