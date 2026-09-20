from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from django.test import TestCase

from qbet.storage.models import ExecutionRecordRow, ModeWorkQueueRow, MonitoringRecordRow
from qbet.storage.observability import PostgresObservabilityRepository

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


class ObservabilityRepositoryTests(TestCase):
    def test_durable_projection_survives_repository_recreation_and_insert_order(self) -> None:
        MonitoringRecordRow.objects.create(
            correlation_id=CORRELATION_ID,
            occurred_at=NOW + timedelta(minutes=2),
            payload={
                "engine": "sports_capital",
                "mode": "execution",
                "level": "warning",
                "status": "failed",
            },
        )
        MonitoringRecordRow.objects.create(
            correlation_id=CORRELATION_ID,
            occurred_at=NOW,
            payload={
                "engine": "bonus",
                "mode": "simulation",
                "level": "info",
                "duration_ms": 25,
                "status": "allow",
            },
        )
        ModeWorkQueueRow.objects.create(
            work_id=uuid4(),
            correlation_id=CORRELATION_ID,
            mode="execution",
            state="recheck",
            scheduled_for=NOW,
            payload={},
        )
        ExecutionRecordRow.objects.create(
            record_id=uuid4(),
            correlation_id=CORRELATION_ID,
            mode="execution",
            state="awaiting_approval",
            payload={},
        )

        first = PostgresObservabilityRepository().snapshot(
            start=NOW - timedelta(minutes=1),
            end=NOW + timedelta(minutes=3),
        )
        second = PostgresObservabilityRepository().snapshot(
            start=NOW - timedelta(minutes=1),
            end=NOW + timedelta(minutes=3),
        )

        self.assertEqual(first, second)
        self.assertEqual(
            first.monitoring_events,
            {
                ("bonus", "simulation", "info"): 1,
                ("sports_capital", "execution", "warning"): 1,
            },
        )
        self.assertEqual(first.monitoring_durations, {("bonus", "simulation"): (1, 25)})
        self.assertEqual(
            first.monitoring_failures,
            {("sports_capital", "execution", "failure"): 1},
        )
        self.assertEqual(first.queue_items, {("execution", "recheck"): 1})
        self.assertEqual(
            first.execution_records,
            {("execution", "awaiting_approval"): 1},
        )
        self.assertEqual(
            first.latest_monitoring_timestamp_seconds,
            int((NOW + timedelta(minutes=2)).timestamp()),
        )

    def test_cumulative_projection_does_not_decay_when_rows_age_out_of_ui_window(self) -> None:
        MonitoringRecordRow.objects.create(
            correlation_id=CORRELATION_ID,
            occurred_at=NOW - timedelta(days=2),
            payload={
                "engine": "bonus",
                "mode": "simulation",
                "level": "info",
                "duration_ms": 10,
                "status": "allow",
            },
        )
        MonitoringRecordRow.objects.create(
            correlation_id=CORRELATION_ID,
            occurred_at=NOW,
            payload={
                "engine": "sports_capital",
                "mode": "execution",
                "level": "error",
                "duration_ms": 30,
                "status": "failed",
            },
        )

        first_scrape = PostgresObservabilityRepository().cumulative_snapshot(end=NOW)
        later_scrape = PostgresObservabilityRepository().cumulative_snapshot(
            end=NOW + timedelta(days=2)
        )
        later_ui_window = PostgresObservabilityRepository().snapshot(
            start=NOW + timedelta(days=1),
            end=NOW + timedelta(days=2),
        )

        self.assertEqual(first_scrape.monitoring_events, later_scrape.monitoring_events)
        self.assertEqual(first_scrape.monitoring_durations, later_scrape.monitoring_durations)
        self.assertEqual(first_scrape.monitoring_failures, later_scrape.monitoring_failures)
        self.assertEqual(
            later_scrape.monitoring_events,
            {
                ("bonus", "simulation", "info"): 1,
                ("sports_capital", "execution", "error"): 1,
            },
        )
        self.assertEqual(
            later_scrape.monitoring_durations,
            {
                ("bonus", "simulation"): (1, 10),
                ("sports_capital", "execution"): (1, 30),
            },
        )
        self.assertEqual(
            later_scrape.monitoring_failures,
            {("sports_capital", "execution", "failure"): 1},
        )
        self.assertEqual(later_ui_window.monitoring_events, {})
        self.assertEqual(later_ui_window.monitoring_durations, {})
        self.assertEqual(later_ui_window.monitoring_failures, {})

    def test_empty_monitoring_window_remains_available_without_fabricated_activity(self) -> None:
        snapshot = PostgresObservabilityRepository().snapshot(
            start=NOW,
            end=NOW + timedelta(minutes=1),
        )

        self.assertTrue(snapshot.available)
        self.assertEqual(snapshot.monitoring_events, {})
        self.assertEqual(snapshot.monitoring_durations, {})
        self.assertIsNone(snapshot.latest_monitoring_timestamp_seconds)
