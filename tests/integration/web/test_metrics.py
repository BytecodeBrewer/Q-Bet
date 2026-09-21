from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from qbet.observability.metrics import ObservabilitySnapshot
from qbet.storage.observability import ObservabilityPersistenceError
from qbet.web.views import _monitoring_query


class _AvailableRepository:
    def cumulative_snapshot(self, *, end) -> ObservabilitySnapshot:
        del end
        return ObservabilitySnapshot(
            available=True,
            monitoring_events={("bonus", "simulation", "info"): 1},
            monitoring_durations={},
            queue_items={},
            execution_records={},
        )

    def snapshot(self, *, start, end) -> ObservabilitySnapshot:
        del start, end
        raise AssertionError("metrics endpoint must use cumulative observability")


class _UnavailableRepository:
    def cumulative_snapshot(self, *, end) -> ObservabilitySnapshot:
        del end
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

    @override_settings(QBET_METRICS_TOKEN="fake-observability-token")
    def test_metrics_auth_failure_is_generic_and_never_echoes_configuration(self) -> None:
        response = self.client.get(
            "/metrics/",
            HTTP_AUTHORIZATION="Bearer wrong-token",
        )

        body = response.content.decode()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(body, "Not found.")
        self.assertNotIn("fake-observability-token", body)
        self.assertNotIn("qbet.web.views.metrics", body)

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


class MonitoringRangeTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def test_seven_day_preset_resolves_to_a_bounded_query(self) -> None:
        query = _monitoring_query(self.factory.get("/monitoring/", {"range": "7d"}))

        self.assertEqual(query.end - query.start, timedelta(days=7))

    def test_custom_range_over_31_days_is_rejected(self) -> None:
        request = self.factory.get(
            "/monitoring/",
            {
                "range": "custom",
                "start": "2026-08-01T00:00:00+00:00",
                "end": "2026-09-02T00:00:00+00:00",
            },
        )

        with self.assertRaises(Http404):
            _monitoring_query(request)


class InfrastructureLinkTests(TestCase):
    def setUp(self) -> None:
        self.staff = User.objects.create_superuser(
            username="observability-admin",
            password="password",
            email="admin@example.test",
        )
        self.user = User.objects.create_user(
            username="observability-user",
            password="password",
        )

    @override_settings(
        QBET_GRAFANA_URL="https://grafana.example.test",
        QBET_VERCEL_DASHBOARD_URL="https://vercel.example.test",
        QBET_SUPABASE_DASHBOARD_URL="https://supabase.example.test",
    )
    def test_infrastructure_links_are_staff_only_and_configuration_driven(self) -> None:
        self.client.force_login(self.staff)
        admin = self.client.get("/admin-area/")
        dashboard = self.client.get("/dashboard/")

        self.assertEqual(admin.status_code, 200)
        self.assertContains(admin, "https://grafana.example.test")
        self.assertContains(admin, "https://vercel.example.test")
        self.assertContains(admin, "https://supabase.example.test")
        self.assertNotContains(dashboard, "https://grafana.example.test")

        self.client.force_login(self.user)
        denied = self.client.get("/admin-area/")
        self.assertEqual(denied.status_code, 302)
        self.assertNotIn("grafana.example.test", denied.content.decode())
