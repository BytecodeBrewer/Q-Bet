from datetime import timedelta
from uuid import UUID, uuid4

from django.test import TestCase, override_settings
from django.utils import timezone

from qbet.monitoring import MonitoringLevel, MonitoringRecord
from qbet.storage.models import ModeWorkQueueRow
from qbet.storage.monitoring import PostgresMonitoringRepository


class PrometheusMetricsTests(TestCase):
    @override_settings(QBET_METRICS_ENABLED=False)
    def test_metrics_endpoint_is_disabled_by_default(self) -> None:
        self.assertEqual(self.client.get("/metrics/").status_code, 404)

    @override_settings(QBET_METRICS_ENABLED=True)
    def test_metrics_are_low_cardinality_projections_of_durable_state(self) -> None:
        now = timezone.now()
        correlation_id = UUID("12345678-1234-5678-1234-567812345678")
        work_id = uuid4()
        repository = PostgresMonitoringRepository()
        repository.append(
            MonitoringRecord(
                correlation_id=correlation_id,
                occurred_at=now - timedelta(seconds=2),
                engine="bonus",
                mode="simulation",
                stage="settlement",
                event_type="lifecycle",
                status="completed",
                duration_ms=125,
                references={
                    "work_id": str(work_id),
                    "opportunity_id": "private-opportunity",
                    "user_id": "private-user",
                },
            )
        )
        repository.append(
            MonitoringRecord(
                correlation_id=correlation_id,
                occurred_at=now - timedelta(seconds=1),
                engine="bonus",
                mode="execution",
                stage="request_handler",
                event_type="stage",
                status="rejected",
                level=MonitoringLevel.WARNING,
                reason_code="provider_specific_reason",
                duration_ms=40,
                references={"account_id": "private-account"},
            )
        )
        ModeWorkQueueRow.objects.create(
            work_id=work_id,
            correlation_id=correlation_id,
            mode="execution",
            state="pending",
            scheduled_for=now,
            payload={"opportunity_id": "private-opportunity"},
        )

        response = self.client.get("/metrics/")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("qbet_monitoring_events_window", body)
        self.assertIn('mode="simulation"', body)
        self.assertIn('mode="execution"', body)
        self.assertIn("qbet_stage_duration_observations_ms", body)
        self.assertIn('qbet_queue_items{mode="execution",state="pending"} 1', body)
        self.assertIn(
            'qbet_operational_issues_window{level="warning",stage="request_handler"} 1',
            body,
        )
        self.assertIn(
            'qbet_lifecycle_outcomes_window{mode="simulation",status="completed"} 1',
            body,
        )
        self.assertNotIn(str(correlation_id), body)
        self.assertNotIn(str(work_id), body)
        self.assertNotIn("private-opportunity", body)
        self.assertNotIn("private-user", body)
        self.assertNotIn("private-account", body)
        self.assertNotIn("provider_specific_reason", body)
