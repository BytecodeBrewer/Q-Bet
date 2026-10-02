from __future__ import annotations

import io
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from qbet.layers import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import SimulationReport
from qbet.simulation import SimulationEngine, SimulationRunConfig, SimulationStatus
from qbet.web.logging import SafeRequestJSONFormatter
from qbet.web.monitoring import MonitoringService
from qbet.web.readiness import PersistenceReadiness, PersistenceReadinessCode
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
        with (
            patch(
                "qbet.web.views.persistence_readiness",
                return_value=PersistenceReadiness(
                    PersistenceReadinessCode.READY,
                    routing="ready",
                    approvals="ready",
                    monitoring="ready",
                ),
            ),
            patch("qbet.web.views.deployment_release_id", return_value="unknown"),
        ):
            response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "q-bet-web",
                "persistence": "ready",
                "readiness": "ready",
                "runtime": {
                    "routing": "ready",
                    "approvals": "ready",
                    "monitoring": "ready",
                },
                "release": "unknown",
            },
        )

    def test_home_renders_product_story_without_operational_or_staff_state(self) -> None:
        response = self.client.get("/")

        self.assertContains(response, "Q-Bet")
        self.assertContains(response, "Market data in. Strategy result out.")
        self.assertContains(response, "BonusEngine")
        self.assertContains(response, "SportsCapitalEngine")
        self.assertContains(response, "SportsExchangeEngine")
        self.assertContains(response, "TicketEngine")
        self.assertContains(response, "PredictionMarketEngine")
        self.assertContains(response, "CryptoYieldEngine")
        self.assertContains(response, "MLEdgeLayer")
        self.assertContains(response, "Risk")
        self.assertContains(response, "Liquidity")
        self.assertContains(response, "Simulation")
        self.assertContains(response, "Approval")
        self.assertContains(response, "Execution")
        self.assertContains(response, "Current vs future")
        self.assertContains(response, "data-home-flow")
        self.assertContains(response, 'id="flow-bonus-path"')
        self.assertContains(response, 'id="flow-sports-path"')
        self.assertContains(response, 'id="flow-shared-path"')
        self.assertContains(response, 'data-flow-station="simulation"')
        self.assertContains(response, 'data-flow-station="approval"')
        self.assertContains(response, 'data-flow-station="execution"')
        self.assertContains(response, "data-flow-packet", count=4)
        self.assertContains(response, "qbet_web/home.js")
        self.assertContains(response, "Sign in")
        self.assertContains(response, "Create account")
        self.assertNotContains(response, "Active matches")
        self.assertNotContains(response, "Pending matches")
        self.assertNotContains(response, "Warnings / errors")
        self.assertNotContains(response, "Current state")
        self.assertNotContains(response, "Operator shortcuts")
        self.assertNotContains(response, 'aria-label="Engine status legend"')
        self.assertNotContains(response, 'href="/monitoring/"')
        self.assertNotContains(response, 'href="/admin-area/"')
        self.assertNotContains(response, 'href="/admin/"')
        self.assertNotContains(response, "Kubernetes")
        self.assertNotContains(response, "Azure")
        self.assertNotContains(response, "BaseEngine")
        self.assertNotContains(response, ">YieldEngine<")
        self.assertNotContains(response, "AlphaEngine")

    def test_product_styles_respect_reduced_motion(self) -> None:
        stylesheet = Path(settings.BASE_DIR, "static", "qbet_web", "app.css").read_text(
            encoding="utf-8"
        )
        visual_stylesheet = Path(
            settings.BASE_DIR,
            "static",
            "qbet_web",
            "phase2_visual.css",
        ).read_text(encoding="utf-8")

        self.assertIn("@media (prefers-reduced-motion: reduce)", stylesheet)
        self.assertIn("transition-duration: .001ms", stylesheet)
        self.assertIn("@media (prefers-reduced-motion: reduce)", visual_stylesheet)
        self.assertIn("animation: none !important;", visual_stylesheet)
        self.assertIn("transition: none !important;", visual_stylesheet)

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

    def test_correlation_id_is_propagated_and_log_excludes_secret_bearing_data(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(SafeRequestJSONFormatter())
        request_logger = logging.getLogger("qbet.web.request")
        request_logger.addHandler(handler)
        correlation_id = "123e4567-e89b-12d3-a456-426614174000"
        try:
            with patch(
                "qbet.web.views.persistence_readiness",
                return_value=PersistenceReadiness(
                    PersistenceReadinessCode.READY,
                    routing="ready",
                    approvals="ready",
                    monitoring="ready",
                ),
            ):
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
        self.assertNotIn("query_count", payload)
        self.assertNotIn("query_duration_ms", payload)
        self.assertNotIn("password", stream.getvalue())
        self.assertNotIn("authorization", stream.getvalue().lower())
        self.assertNotIn("cookie", stream.getvalue().lower())
        self.assertNotIn("bank", stream.getvalue().lower())
        self.assertNotIn("bookmaker", stream.getvalue().lower())


    @override_settings(QBET_PROFILE_WEB_REQUESTS=True)
    def test_opt_in_query_profile_logs_only_aggregate_metrics(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(SafeRequestJSONFormatter())
        request_logger = logging.getLogger("qbet.web.request")
        request_logger.addHandler(handler)
        try:
            with patch(
                "qbet.web.views.persistence_readiness",
                return_value=PersistenceReadiness(
                    PersistenceReadinessCode.READY,
                    routing="ready",
                    approvals="ready",
                    monitoring="ready",
                ),
            ):
                response = self.client.get("/health/")
        finally:
            request_logger.removeHandler(handler)

        payload = json.loads(stream.getvalue())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["query_count"], 0)
        self.assertGreaterEqual(payload["query_duration_ms"], 0)
        self.assertNotIn("sql", payload)
        self.assertNotIn("params", payload)


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


def test_monitoring_service_uses_shared_postgres_history() -> None:
    snapshot = _monitoring_service().snapshot()

    assert snapshot.history_available is True


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
