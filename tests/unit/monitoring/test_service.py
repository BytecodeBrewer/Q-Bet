from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.test import TestCase

from qbet.monitoring import MonitoringLevel, MonitoringQuery, MonitoringRecord, MonitoringService
from qbet.storage.monitoring import PostgresMonitoringRepository


class MonitoringServiceTests(TestCase):
    def test_redacts_sensitive_references_and_returns_chronological_compact_projection(self) -> None:
        repository = PostgresMonitoringRepository()
        correlation_id = UUID("12345678-1234-5678-1234-567812345678")
        start = datetime(2026, 9, 7, 10, tzinfo=UTC)
        repository.append(
            MonitoringRecord(
                correlation_id=correlation_id,
                occurred_at=start + timedelta(minutes=1),
                engine="bonus",
                mode="simulation",
                stage="calculation",
                event_type="stage",
                status="allow",
                duration_ms=25,
                references={
                    "session_token": "never-store-this",
                    "raw_payload": {"odds": "secret"},
                    "message": "Authorization: Bearer top-secret-token",
                    "error": 'Traceback (most recent call last): File "worker.py", line 9',
                    "work_id": "work-1",
                    "opportunity_id": "opportunity-1",
                },
            )
        )
        repository.append(
            MonitoringRecord(
                correlation_id=correlation_id,
                occurred_at=start,
                engine="bonus",
                mode="simulation",
                stage="data_aggregation",
                event_type="stage",
                status="allow",
                level=MonitoringLevel.WARNING,
                reason_code="provider_delayed",
            )
        )

        query = MonitoringQuery(start=start, end=start + timedelta(hours=1))
        service = MonitoringService(repository)

        extended = service.extended(query)
        compact = service.compact(query)

        self.assertEqual(
            [record.stage for record in extended],
            ["data_aggregation", "calculation"],
        )
        self.assertEqual(extended[1].references["session_token"], "[redacted]")
        self.assertEqual(extended[1].references["raw_payload"], "[redacted]")
        self.assertEqual(extended[1].references["message"], "[redacted]")
        self.assertEqual(extended[1].references["error"], "[redacted]")
        self.assertEqual(extended[1].references["work_id"], "work-1")
        self.assertEqual(compact[0].correlation_id, correlation_id)
        self.assertEqual(compact[0].opportunity_id, "opportunity-1")
        self.assertEqual(compact[0].started_at, start)
        self.assertEqual(compact[0].finished_at, start + timedelta(minutes=1))
        self.assertEqual(compact[0].duration_ms, 25)
        self.assertEqual(compact[0].warning_count, 1)

    def test_correlation_filter_and_empty_range_use_same_persisted_source(self) -> None:
        repository = PostgresMonitoringRepository()
        start = datetime(2026, 9, 7, 10, tzinfo=UTC)
        first = UUID("12345678-1234-5678-1234-567812345678")
        second = UUID("87654321-4321-8765-4321-876543218765")
        for correlation_id, minute in ((first, 1), (second, 2)):
            repository.append(
                MonitoringRecord(
                    correlation_id=correlation_id,
                    occurred_at=start + timedelta(minutes=minute),
                    engine="bonus",
                    mode="execution",
                    stage="queue",
                    event_type="state_transition",
                    status="completed",
                )
            )

        service = MonitoringService(repository)
        filtered = MonitoringQuery(
            start=start,
            end=start + timedelta(hours=1),
            correlation_id=first,
        )
        empty = MonitoringQuery(
            start=start + timedelta(days=1),
            end=start + timedelta(days=1, hours=1),
        )

        self.assertEqual(
            tuple(record.correlation_id for record in service.extended(filtered)),
            (first,),
        )
        self.assertEqual(
            tuple(process.correlation_id for process in service.compact(filtered)),
            (first,),
        )
        self.assertEqual(service.extended(empty), ())
        self.assertEqual(service.compact(empty), ())

    def test_rejects_reversed_and_oversized_ranges(self) -> None:
        start = datetime(2026, 9, 7, tzinfo=UTC)
        with self.assertRaisesMessage(ValueError, "end must not be before start"):
            MonitoringQuery(start=start, end=start - timedelta(seconds=1))
        with self.assertRaisesMessage(ValueError, "monitoring range must not exceed 31 days"):
            MonitoringQuery(start=start, end=start + timedelta(days=32))
