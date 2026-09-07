from datetime import UTC, datetime
from uuid import UUID

from django.contrib.auth import get_user_model
from django.test import TestCase

from qbet.monitoring import MonitoringRecord
from qbet.storage.monitoring import PostgresMonitoringRepository


class MonitoringExportTests(TestCase):
    def setUp(self) -> None:
        self.staff = get_user_model().objects.create_user("staff", password="test", is_staff=True)
        self.user = get_user_model().objects.create_user("user", password="test")
        PostgresMonitoringRepository().append(
            MonitoringRecord(
                correlation_id=UUID("12345678-1234-5678-1234-567812345678"),
                occurred_at=datetime(2026, 9, 7, 10, tzinfo=UTC),
                engine="sports_capital",
                mode="execution",
                stage="liquidity_check",
                event_type="stage",
                status="allow",
                references={"credential": "redacted"},
            )
        )

    def test_staff_can_view_and_export_compact_or_extended_monitoring(self) -> None:
        self.client.force_login(self.staff)
        query = "?start=2026-09-07T09:00:00%2B00:00&end=2026-09-07T11:00:00%2B00:00"

        compact = self.client.get("/monitoring/" + query)
        extended = self.client.get("/monitoring/?view=extended&" + query[1:])
        csv_export = self.client.get("/monitoring/export/csv/" + query)
        json_export = self.client.get("/monitoring/export/json/?view=extended&" + query[1:])

        self.assertContains(compact, "sports_capital")
        self.assertContains(extended, "liquidity_check")
        self.assertEqual(csv_export.status_code, 200)
        self.assertEqual(json_export.status_code, 200)
        self.assertIn("[redacted]", json_export.content.decode())

    def test_normal_user_cannot_read_or_export_monitoring(self) -> None:
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/monitoring/").status_code, 302)
        self.assertEqual(self.client.get("/monitoring/export/json/").status_code, 302)
