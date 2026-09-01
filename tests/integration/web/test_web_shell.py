from __future__ import annotations

import io
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

django.setup()

from django.conf import settings
from django.test import Client, SimpleTestCase

from qbet.layers import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import SimulationReport
from qbet.simulation import SimulationEngine, SimulationRunConfig, SimulationStatus
from qbet.web.logging import SafeRequestJSONFormatter
from qbet.web.monitoring import MonitoringService
from qbet.web.settings import parse_allowed_hosts


class WebShellSmokeTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_health_route_returns_small_json_status(self) -> None:
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "q-bet-web"})

    def test_home_renders_static_engine_statuses_without_domain_calculations(
        self,
    ) -> None:
        response = self.client.get("/")

        self.assertContains(response, "Q-Bet")
        self.assertContains(response, "BonusEngine")
        self.assertContains(response, "SportsCapitalEngine")
        self.assertContains(response, 'status-green')
        self.assertContains(response, 'status-amber')
        self.assertNotContains(response, "Base")
        self.assertNotContains(response, "Yield")
        self.assertNotContains(response, "Alpha")

    def test_account_boundary_requires_django_authentication(self) -> None:
        response = self.client.get("/account/")

        self.assertRedirects(
            response, "/accounts/login/?next=/account/", fetch_redirect_response=False
        )

    def test_security_and_csrf_middleware_are_enabled(self) -> None:
        self.assertIn(
            "django.middleware.security.SecurityMiddleware", settings.MIDDLEWARE
        )
        self.assertIn("django.middleware.csrf.CsrfViewMiddleware", settings.MIDDLEWARE)

        response = self.client.get("/accounts/login/")
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_correlation_id_is_propagated_and_log_excludes_secret_bearing_data(
        self,
    ) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(SafeRequestJSONFormatter())
        request_logger = logging.getLogger("qbet.web.request")
        request_logger.addHandler(handler)
        correlation_id = "123e4567-e89b-12d3-a456-426614174000"
        try:
            response = self.client.get(
                "/health/?password=not-for-logs",
                HTTP_X_CORRELATION_ID=correlation_id,
                HTTP_AUTHORIZATION="Bearer not-for-logs",
                HTTP_X_BANK_DETAILS="not-for-logs",
                HTTP_X_BOOKMAKER_CREDENTIAL="not-for-logs",
            )
        finally:
            request_logger.removeHandler(handler)

        payload = json.loads(stream.getvalue())
        self.assertEqual(response["X-Correlation-ID"], correlation_id)
        self.assertEqual(payload["method"], "GET")
        self.assertEqual(payload["path"], "/health/")
        self.assertEqual(payload["status"], 200)
        self.assertEqual(payload["correlation_id"], correlation_id)
        self.assertIn("duration_ms", payload)
        self.assertNotIn("password", stream.getvalue())
        self.assertNotIn("authorization", stream.getvalue().lower())
        self.assertNotIn("cookie", stream.getvalue().lower())
        self.assertNotIn("bank", stream.getvalue().lower())
        self.assertNotIn("bookmaker", stream.getvalue().lower())


def test_parses_comma_separated_allowed_hosts_without_whitespace() -> None:
    assert parse_allowed_hosts("example.com, www.example.com, , api.example.com ") == [
        "example.com",
        "www.example.com",
        "api.example.com",
    ]


def test_invalid_host_returns_400_with_correlation_id_before_authentication() -> None:
    correlation_id = "123e4567-e89b-12d3-a456-426614174001"

    response = Client().get(
        "/health/",
        HTTP_HOST="unapproved.example",
        HTTP_X_CORRELATION_ID=correlation_id,
    )

    assert response.status_code == 400
    assert response["X-Correlation-ID"] == correlation_id

class _ReportStore:
    def __init__(
        self,
        reports: tuple[SimulationReport, ...] = (),
        records: tuple[SimulationLogRecord, ...] = (),
    ) -> None:
        self._reports = reports
        self._records = records

    def list_recent_reports(self, limit: int = 20) -> tuple[SimulationReport, ...]:
        return self._reports[:limit]

    def load_records(self, run_id: object) -> tuple[SimulationLogRecord, ...]:
        return tuple(record for record in self._records if record.run_id == run_id)


def _report() -> SimulationReport:
    return SimulationReport(
        run_id=uuid4(),
        config=SimulationRunConfig(
            engine=SimulationEngine.BONUS,
            starting_capital=Decimal(100),
        ),
        engine="bonus",
        strategy_id=None,
        status=SimulationStatus.COMPLETED,
        starting_capital=Decimal(100),
        current_capital=Decimal(110),
        top_up_total=Decimal(0),
        profit_loss=Decimal(10),
        completed_steps=(),
        elapsed_duration=timedelta(minutes=5),
        progress=Decimal(1),
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_monitoring_renders_configured_empty_state() -> None:
    with patch("qbet.web.views.MONITORING_SERVICE", MonitoringService(_ReportStore())):
        response = Client().get("/monitoring/")

    assert response.status_code == 200
    assert "BonusEngine" in response.content.decode()
    assert "SportsCapitalEngine" in response.content.decode()
    assert "No simulation reports are available yet." in response.content.decode()


def test_monitoring_renders_report_and_safe_warning_summary() -> None:
    report = _report()
    warning = SimulationLogRecord(
        run_id=report.run_id,
        sequence=1,
        timestamp=datetime(2026, 9, 1, tzinfo=UTC),
        record_type=SimulationLogRecordType.WARNING,
        source="simulation.workflow",
        payload={"secret": "not-for-display", "message": "not-for-display"},
    )
    with patch(
        "qbet.web.views.MONITORING_SERVICE",
        MonitoringService(_ReportStore((report,), (warning,))),
    ):
        response = Client().get("/monitoring/")

    content = response.content.decode()
    assert response.status_code == 200
    assert "bonus" in content
    assert "completed" in content
    assert "110" in content
    assert "10" in content
    assert "Latest simulation recorded a warning." in content
    assert "not-for-display" not in content

def test_monitoring_renders_error_summary() -> None:
    report = _report()
    error = SimulationLogRecord(
        run_id=report.run_id,
        sequence=1,
        timestamp=datetime(2026, 9, 1, tzinfo=UTC),
        record_type=SimulationLogRecordType.ERROR,
        source="simulation.workflow",
        payload={"token": "not-for-display"},
    )
    with patch(
        "qbet.web.views.MONITORING_SERVICE",
        MonitoringService(_ReportStore((report,), (error,))),
    ):
        response = Client().get("/monitoring/")

    content = response.content.decode()
    assert "Latest simulation recorded an error." in content
    assert "not-for-display" not in content

def test_monitoring_renders_unconfigured_history_state() -> None:
    response = Client().get("/monitoring/")

    assert response.status_code == 200
    assert "Simulation history is not configured." in response.content.decode()