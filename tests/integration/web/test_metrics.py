from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from qbet.observability.metrics import ObservabilitySnapshot
from qbet.storage.observability import ObservabilityPersistenceError


class _AvailableRepository:
    def snapshot(self, *, start, end) -> ObservabilitySnapshot:
        del start, end
        return ObservabilitySnapshot(
            available=True,
            monitoring_events={("bonus", "simulation", "info"): 1},
            monitoring_durations={},
            queue_items={},
            execution_records={},
        )


class _UnavailableRepository:
    def snapshot(self, *, start, end) -> ObservabilitySnapshot:
        del start, end
        raise ObservabilityPersistenceError("database unavailable")


class MetricsEndpointTests(SimpleTestCase):
    def test_metrics_is_not_routable_without_a_token(self) -> None:
        response = self.client.get("/metrics/")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn("metrics", response.content.decode().lower())

    @override_settings(QBET_METRICS_TOKEN="test-token")
    def test_metrics_requires_the_configured_bearer_token(self) -> None:
        for header in (None, "Bearer incorrect"):
            with self.subTest(header=header):
                response = self.client.get(
                    "/metrics/",
                    HTTP_AUTHORIZATION=header or "",
                )
                self.assertEqual(response.status_code, 404)

    @override_settings(QBET_METRICS_TOKEN="test-token")
    def test_metrics_returns_aggregate_prometheus_text_for_a_valid_token(self) -> None:
        with patch("qbet.web.views.OBSERVABILITY_REPOSITORY", _AvailableRepository()):
            response = self.client.get("/metrics/", HTTP_AUTHORIZATION="Bearer test-token")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; version=0.0.4; charset=utf-8")
        self.assertIn("qbet_monitoring_events_total", response.content.decode())

    @override_settings(QBET_METRICS_TOKEN="test-token")
    def test_metrics_reports_a_degraded_source_without_exposing_the_database_error(self) -> None:
        with patch("qbet.web.views.OBSERVABILITY_REPOSITORY", _UnavailableRepository()):
            response = self.client.get("/metrics/", HTTP_AUTHORIZATION="Bearer test-token")

        self.assertEqual(response.status_code, 200)
        self.assertIn("qbet_observability_source_available 0", response.content.decode())
        self.assertNotIn("database unavailable", response.content.decode())
