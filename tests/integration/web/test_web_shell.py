from __future__ import annotations

import io
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from qbet.layers import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import SimulationReport
from qbet.simulation import SimulationEngine, SimulationRunConfig, SimulationStatus
from qbet.web.logging import SafeRequestJSONFormatter
from qbet.web.monitoring import MonitoringService
from qbet.web.settings import parse_allowed_hosts
from qbet.web.views import _monitoring_service

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

django.setup()


class WebShellSmokeTests(SimpleTestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_health_route_returns_small_json_status(self) -> None:
        response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "q-bet-web"})

    def test_home_renders_execution_statuses_without_internal_monitoring_link(
        self,
    ) -> None:
        response = self.client.get("/")

        self.assertContains(response, "Q-Bet")
        self.assertContains(response, "BonusEngine")
        self.assertContains(response, "SportsCapitalEngine")
        self.assertContains(response, "Sign in")
        self.assertNotContains(response, "Open monitoring")
        self.assertContains(response, "status-gray")
        self.assertNotContains(response, "Base")
        self.assertNotContains(response, "Yield")
        self.assertNotContains(response, "Alpha")

    def test_account_boundary_requires_django_authentication(self) -> None:
        response = self.client.get("/account/")

        self.assertRedirects(
            response, "/accounts/login/?next=/account/", fetch_redirect_response=False
        )

    def test_security_and_csrf_middleware_are_enabled(self) -> None:
        self.assertIn("django.middleware.security.SecurityMiddleware", settings.MIDDLEWARE)
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

    def load_records(self, run_id: UUID) -> tuple[SimulationLogRecord, ...]:
        return tuple(record for record in self._records if record.run_id == run_id)

    def load_report(self, run_id: UUID) -> SimulationReport:
        for report in self._reports:
            if report.run_id == run_id:
                return report
        raise KeyError(run_id)


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


def test_monitoring_snapshot_renders_configured_empty_state() -> None:
    snapshot = MonitoringService(_ReportStore()).snapshot()

    assert snapshot.history_available is True
    assert tuple(engine.name for engine in snapshot.engines) == (
        "BonusEngine",
        "SportsCapitalEngine",
    )
    assert not snapshot.reports


def test_monitoring_snapshot_renders_safe_warning_summary() -> None:
    report = _report()
    warning = SimulationLogRecord(
        run_id=report.run_id,
        sequence=1,
        timestamp=datetime(2026, 9, 1, tzinfo=UTC),
        record_type=SimulationLogRecordType.WARNING,
        source="simulation.workflow",
        payload={"secret": "not-for-display", "message": "not-for-display"},
    )

    snapshot = MonitoringService(_ReportStore((report,), (warning,))).snapshot()

    assert snapshot.latest_alert is not None
    assert snapshot.latest_alert.summary == "Latest simulation recorded a warning."
    assert snapshot.engines[0].warning_count == 1


def test_monitoring_snapshot_renders_error_summary() -> None:
    report = _report()
    error = SimulationLogRecord(
        run_id=report.run_id,
        sequence=1,
        timestamp=datetime(2026, 9, 1, tzinfo=UTC),
        record_type=SimulationLogRecordType.ERROR,
        source="simulation.workflow",
        payload={"token": "not-for-display"},
    )

    snapshot = MonitoringService(_ReportStore((report,), (error,))).snapshot()

    assert snapshot.latest_alert is not None
    assert snapshot.latest_alert.summary == "Latest simulation recorded an error."
    assert snapshot.engines[0].error_count == 1


def test_monitoring_snapshot_renders_unconfigured_history_state() -> None:
    snapshot = MonitoringService().snapshot()

    assert snapshot.history_available is False
    assert snapshot.engines[0].detail == "Simulation history is not configured."


def test_configured_read_only_history_with_missing_schema_is_unavailable() -> None:
    with TemporaryDirectory() as directory:
        database_path = Path(directory) / "simulation-history.sqlite3"
        database_path.touch()
        with override_settings(QBET_SIMULATION_REPORT_DB=database_path):
            snapshot = _monitoring_service().snapshot()

        assert snapshot.history_available is False
        assert database_path.exists() is True


def test_monitoring_uses_newest_report_for_engine_status() -> None:
    older_report = _report()
    newer_report = older_report.model_copy(
        update={
            "status": SimulationStatus.RUNNING,
            "generated_at": datetime(2026, 9, 2, tzinfo=UTC),
        }
    )

    snapshot = MonitoringService(_ReportStore((newer_report, older_report))).snapshot()

    bonus_engine = next(engine for engine in snapshot.engines if engine.name == "BonusEngine")
    assert bonus_engine.status == "green"
    assert bonus_engine.detail == "Simulation running."
