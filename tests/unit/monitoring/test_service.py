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
                references={"session_token": "never-store-this", "work_id": "work-1"},
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

        self.assertEqual([record.stage for record in extended], ["data_aggregation", "calculation"])
        self.assertEqual(extended[1].references["session_token"], "[redacted]")
        self.assertEqual(compact[0].correlation_id, correlation_id)
        self.assertEqual(compact[0].warning_count, 1)

    def test_rejects_reversed_and_oversized_ranges(self) -> None:
        start = datetime(2026, 9, 7, tzinfo=UTC)
        with self.assertRaisesMessage(ValueError, "end must not be before start"):
            MonitoringQuery(start=start, end=start - timedelta(seconds=1))
        with self.assertRaisesMessage(ValueError, "monitoring range must not exceed 31 days"):
            MonitoringQuery(start=start, end=start + timedelta(days=32))
