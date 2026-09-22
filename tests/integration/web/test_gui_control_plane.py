from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from qbet.layers import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import (
    CustomerReportAmount,
    CustomerReportInput,
    CustomerReportingService,
    SimulationReport,
)
from qbet.simulation import SimulationEngine, SimulationRunConfig, SimulationStatus
from qbet.web.models import CustomerReportAccess, SimulationAvailability
from qbet.web.monitoring import MonitoringService
from qbet.workflow.routing import EngineModes, RoutingConfiguration


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

django.setup()


class _ReportStore:
    def __init__(
        self,
        reports: tuple[SimulationReport, ...],
        records: tuple[SimulationLogRecord, ...],
    ) -> None:
        self._reports = reports
        self._records = records

    def list_recent_reports(self, limit: int = 20) -> tuple[SimulationReport, ...]:
        return self._reports[:limit]

    def load_report(self, run_id: UUID) -> SimulationReport:
        for report in self._reports:
            if report.run_id == run_id:
                return report
        raise KeyError(run_id)

    def load_records(self, run_id: UUID) -> tuple[SimulationLogRecord, ...]:
        records = tuple(record for record in self._records if record.run_id == run_id)
        if not records:
            raise KeyError(run_id)
        return records


def _report(
    engine: SimulationEngine,
    *,
    status: SimulationStatus = SimulationStatus.COMPLETED,
    currency: str = "EUR",
) -> SimulationReport:
    return SimulationReport(
        run_id=uuid4(),
        config=SimulationRunConfig(engine=engine, starting_capital=Decimal(100)),
        engine=engine.value,
        strategy_id=None,
        status=status,
        starting_capital=Decimal(100),
        current_capital=Decimal(112),
        top_up_total=Decimal(0),
        profit_loss=Decimal(12),
        completed_steps=(),
        elapsed_duration=timedelta(minutes=5),
        progress=Decimal(1),
        generated_at=datetime(2026, 9, 2, tzinfo=UTC),
        customer_report_input=CustomerReportInput(
            match="Northbridge v Riverside",
            provider="Bookmaker A",
            counterparty_provider="Exchange B",
            strategy="Qualifying bet",
            assigned_amounts=(
                CustomerReportAmount(label="Back stake", amount=Decimal("50")),
                CustomerReportAmount(label="Lay stake", amount=Decimal("48.25")),
            ),
            invested_capital=Decimal("50"),
            currency=currency,
        ),
    )


def _records(report: SimulationReport) -> tuple[SimulationLogRecord, ...]:
    return (
        SimulationLogRecord(
            run_id=report.run_id,
            sequence=1,
            timestamp=datetime(2026, 9, 2, tzinfo=UTC),
            record_type=SimulationLogRecordType.WORKFLOW_TRANSITION,
            source="workflow.orchestrator",
            payload={
                "stage": "data_aggregation",
                "decision": "allow",
                "secret": "workflow-secret",
            },
        ),
        SimulationLogRecord(
            run_id=report.run_id,
            sequence=2,
            timestamp=datetime(2026, 9, 2, tzinfo=UTC),
            record_type=SimulationLogRecordType.WARNING,
            source="workflow.orchestrator",
            payload={
                "message": "safe warning",
                "credential": "warning-credential",
                "account_id": "warning-account",
            },
        ),
        SimulationLogRecord(
            run_id=report.run_id,
            sequence=3,
            timestamp=datetime(2026, 9, 2, tzinfo=UTC),
            record_type=SimulationLogRecordType.ERROR,
            source="workflow.orchestrator",
            payload={"message": "safe error", "token": "error-token"},
        ),
        SimulationLogRecord(
            run_id=report.run_id,
            sequence=4,
            timestamp=datetime(2026, 9, 2, tzinfo=UTC),
            record_type=SimulationLogRecordType.RISK_DECISION,
            source="workflow.orchestrator",
            payload={
                "decision": "allow",
                "account_identifier": "risk-account",
            },
        ),
        SimulationLogRecord(
            run_id=report.run_id,
            sequence=5,
            timestamp=datetime(2026, 9, 2, tzinfo=UTC),
            record_type=SimulationLogRecordType.RAW_INPUT,
            source="simulation.runner",
            payload={"credential": "raw-input-secret", "market": "fixture"},
        ),
    )


class GuiControlPlaneTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("member", password="Strong-pass-123")
        self.other_user = User.objects.create_user("other-member", password="Strong-pass-123")
        self.staff = User.objects.create_user("staff", password="Strong-pass-123", is_staff=True)
        self.bonus_report = _report(SimulationEngine.BONUS, status=SimulationStatus.RUNNING)
        self.sports_report = _report(SimulationEngine.SPORTS_CAPITAL)
        self.report_store = _ReportStore(
            (self.bonus_report, self.sports_report),
            _records(self.bonus_report) + _records(self.sports_report),
        )
        self.service = MonitoringService(self.report_store)
        self.monitoring_patch = patch("qbet.web.views.MONITORING_SERVICE", self.service)
        self.monitoring_patch.start()
        self.addCleanup(self.monitoring_patch.stop)
        self.reporting_patch = patch(
            "qbet.web.views.CUSTOMER_REPORTING_SERVICE",
            CustomerReportingService(self.report_store),
        )
        self.reporting_patch.start()
        self.addCleanup(self.reporting_patch.stop)
        SimulationAvailability.objects.all().delete()

    def test_dashboard_is_protected_and_normal_user_sees_execution_only(self) -> None:
        self.assertRedirects(
            self.client.get("/dashboard/"),
            "/accounts/login/?next=/dashboard/",
            fetch_redirect_response=False,
        )
        self.client.force_login(self.user)

        response = self.client.get("/dashboard/")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(content.count("data-engine-widget="), 2)
        self.assertNotIn("data-simulation-engine=", content)
        self.assertNotIn("data-legacy-", content)
        self.assertEqual(content.count('data-engine-widget="bonus"'), 1)
        self.assertEqual(content.count('data-engine-widget="sports_capital"'), 1)
        self.assertContains(response, "Execution idle")
        self.assertContains(response, "No known issues")
        self.assertNotContains(response, 'aria-label="Enable BonusEngine execution"')
        self.assertNotContains(response, "Simulation reports")
        self.assertNotContains(response, "BaseEngine")
        self.assertNotContains(response, "YieldEngine")
        self.assertNotContains(response, "AlphaEngine")

    def test_staff_dashboard_keeps_execution_and_simulation_summaries_separate(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.staff)

        response = self.client.get("/dashboard/")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(content.count("data-engine-widget="), 2)
        self.assertEqual(content.count("data-simulation-engine="), 2)
        self.assertNotIn("data-legacy-", content)
        self.assertIn("<span>Recorded activity</span><strong>0</strong>", content)
        self.assertIn("<span>Recorded runs</span><strong>2</strong>", content)
        self.assertContains(response, "Customer plane")
        self.assertContains(response, "Admin only")
        self.assertContains(response, "Deterministic pipeline test")
        self.assertContains(response, "Run pipeline test")

    def test_historical_incidents_do_not_poison_current_runtime_state(self) -> None:
        report = _report(SimulationEngine.BONUS)
        service = MonitoringService(_ReportStore((report,), _records(report)))
        active_configuration = RoutingConfiguration(
            bonus=EngineModes(simulation=True)
        )

        active = service.snapshot(runtime_configuration=active_configuration)
        active_bonus = next(engine for engine in active.engines if engine.engine_id == "bonus")

        self.assertEqual(active_bonus.status, "green")
        self.assertTrue(active_bonus.enabled)
        self.assertFalse(active_bonus.active)
        self.assertEqual(active_bonus.live_state, "ready")
        self.assertEqual(active_bonus.detail, "Ready.")
        self.assertEqual(active_bonus.warning_count, 1)
        self.assertEqual(active_bonus.error_count, 1)
        self.assertTrue(active_bonus.workflow_stages)
        self.assertTrue(all(stage.status == "green" for stage in active_bonus.workflow_stages))

        inactive = service.snapshot(runtime_configuration=RoutingConfiguration())
        inactive_bonus = next(engine for engine in inactive.engines if engine.engine_id == "bonus")
        self.assertEqual(inactive_bonus.status, "gray")
        self.assertEqual(inactive_bonus.live_state, "inactive")
        self.assertTrue(all(stage.status == "gray" for stage in inactive_bonus.workflow_stages))

        unavailable = service.snapshot(
            runtime_configuration=active_configuration,
            runtime_available=False,
        )
        unavailable_bonus = next(
            engine for engine in unavailable.engines if engine.engine_id == "bonus"
        )
        self.assertEqual(unavailable_bonus.status, "red")
        self.assertEqual(unavailable_bonus.live_state, "error")
        self.assertTrue(
            all(stage.status == "red" for stage in unavailable_bonus.workflow_stages)
        )

    def test_old_run_transition_does_not_override_current_running_readiness(self) -> None:
        current = _report(SimulationEngine.BONUS, status=SimulationStatus.RUNNING)
        old = _report(SimulationEngine.BONUS)
        old_rejection = SimulationLogRecord(
            run_id=old.run_id,
            sequence=1,
            timestamp=datetime(2026, 9, 1, tzinfo=UTC),
            record_type=SimulationLogRecordType.WORKFLOW_TRANSITION,
            source="workflow.orchestrator",
            payload={
                "stage": "domain_risk",
                "decision": "reject",
                "reason": "historical_rejection",
            },
        )
        service = MonitoringService(_ReportStore((current, old), (old_rejection,)))

        snapshot = service.snapshot(
            runtime_configuration=RoutingConfiguration(
                bonus=EngineModes(simulation=True)
            ),
            runtime_activity={"bonus": (1, 0)},
        )
        bonus = next(engine for engine in snapshot.engines if engine.engine_id == "bonus")
        risk = next(stage for stage in bonus.workflow_stages if stage.name == "Domain risk")

        self.assertEqual(bonus.status, "green")
        self.assertEqual(bonus.detail, "Running.")
        self.assertEqual(risk.status, "green")
        self.assertEqual(risk.detail, "Ready")

    def test_current_running_error_remains_a_current_failure(self) -> None:
        snapshot = self.service.snapshot(
            runtime_configuration=RoutingConfiguration(
                bonus=EngineModes(simulation=True)
            ),
            runtime_activity={"bonus": (1, 0)},
        )
        bonus = next(engine for engine in snapshot.engines if engine.engine_id == "bonus")

        self.assertEqual(bonus.status, "red")
        self.assertEqual(bonus.live_state, "error")
        self.assertEqual(bonus.detail, "Current run error.")
        self.assertEqual(bonus.warning_count, 1)
        self.assertEqual(bonus.error_count, 1)

    def test_engine_detail_hides_monitoring_internals_from_normal_user(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get("/engines/bonus/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operational state")
        self.assertContains(response, "Running matches")
        self.assertContains(response, "Recorded capital")
        self.assertContains(response, "Warnings")
        self.assertContains(response, "Errors")
        self.assertContains(response, "No live execution history source is connected yet")
        self.assertNotContains(response, "Workflow state")
        self.assertNotContains(response, "Data aggregation")
        self.assertNotContains(response, "LiquidityChecker")
        self.assertNotContains(response, "Execution Layer state")
        self.assertNotContains(response, "workflow.orchestrator")
        self.assertNotContains(response, "All BonusEngine reports")

    def test_monitoring_requires_staff_access(self) -> None:
        self.assertRedirects(
            self.client.get("/monitoring/"),
            "/accounts/login/?next=/monitoring/",
            fetch_redirect_response=False,
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/monitoring/").status_code, 302)

        self.client.force_login(self.staff)
        response = self.client.get("/monitoring/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BonusEngine")

    @override_settings(QBET_SIMULATION_MODE_ENABLED=False)
    def test_simulation_visibility_is_admin_only_and_uses_persisted_control(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/simulation/").status_code, 404)
        self.assertNotContains(self.client.get("/dashboard/"), "Run pipeline test")

        self.client.force_login(self.staff)
        simulation = self.client.get("/simulation/")
        dashboard = self.client.get("/dashboard/")
        self.assertEqual(simulation.status_code, 200)
        self.assertEqual(simulation.content.decode().count("data-simulation-engine="), 2)
        self.assertContains(dashboard, "Run pipeline test")

        SimulationAvailability.objects.filter(pk=1).update(enabled=False)
        self.assertEqual(self.client.get("/simulation/").status_code, 404)
        self.assertNotContains(self.client.get("/dashboard/"), "Run pipeline test")

    def test_settings_show_admin_area_only_to_staff(self) -> None:
        self.client.force_login(self.user)
        user_settings = self.client.get("/settings/presentation/")
        self.assertContains(user_settings, "browser session only")
        self.assertNotContains(user_settings, "Admin Area")
        self.assertNotContains(user_settings, "Enable Simulation plane")

        self.client.force_login(self.staff)
        staff_settings = self.client.get("/settings/presentation/")
        self.assertContains(staff_settings, "Admin Area")
        self.assertContains(staff_settings, "Enable Simulation plane")
        self.assertContains(staff_settings, "Admin only")

    def test_presentation_preferences_are_session_scoped_and_disclosed(self) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            "/settings/presentation/", {"theme": "dark", "font_size": "large"}
        )
        self.assertRedirects(response, "/settings/presentation/")
        dashboard = self.client.get("/dashboard/")

        self.assertContains(dashboard, 'data-theme="dark"')
        self.assertContains(dashboard, 'data-font-size="large"')

    def test_dashboard_drag_order_is_session_scoped_and_planes_are_separate(self) -> None:
        self.client.force_login(self.user)
        response = self.client.post(
            "/dashboard/layout/",
            {"plane": "execution", "order": ["sports_capital", "bonus"]},
        )
        self.assertEqual(response.status_code, 200)
        dashboard = self.client.get("/dashboard/")
        content = dashboard.content.decode()
        self.assertLess(
            content.index('data-engine-widget="sports_capital"'),
            content.index('data-engine-widget="bonus"'),
        )

        forbidden = self.client.post(
            "/dashboard/layout/",
            {"plane": "simulation", "order": ["sports_capital", "bonus"]},
        )
        self.assertEqual(forbidden.status_code, 404)

        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.staff)
        allowed = self.client.post(
            "/dashboard/layout/",
            {"plane": "simulation", "order": ["sports_capital", "bonus"]},
        )
        self.assertEqual(allowed.status_code, 200)
        staff_dashboard = self.client.get("/dashboard/").content.decode()
        self.assertLess(
            staff_dashboard.index('data-simulation-engine="sports_capital"'),
            staff_dashboard.index('data-simulation-engine="bonus"'),
        )

    def test_customer_reports_require_an_owner_grant_or_staff_access(self) -> None:
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/reports/?range=30d").status_code, 200)
        self.assertNotContains(self.client.get("/reports/?range=30d"), "Northbridge v Riverside")
        self.assertEqual(self.client.get(f"/reports/{self.sports_report.run_id}/").status_code, 404)
        self.assertEqual(
            self.client.get(f"/reports/{self.sports_report.run_id}/export/json/").status_code,
            404,
        )

        CustomerReportAccess.objects.create(report_id=self.sports_report.run_id, user=self.user)
        history = self.client.get("/reports/?range=30d")
        detail = self.client.get(f"/reports/{self.sports_report.run_id}/")
        csv_export = self.client.get(f"/reports/{self.sports_report.run_id}/export/csv/")
        json_export = self.client.get(f"/reports/{self.sports_report.run_id}/export/json/")
        pdf_export = self.client.get(f"/reports/{self.sports_report.run_id}/export/pdf/")

        self.assertEqual(history.status_code, 200)
        self.assertContains(history, "Northbridge v Riverside")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(csv_export.status_code, 200)
        self.assertEqual(json_export.status_code, 200)
        self.assertEqual(pdf_export.status_code, 200)

        self.client.force_login(self.other_user)
        self.assertNotContains(self.client.get("/reports/?range=30d"), "Northbridge v Riverside")
        self.assertEqual(self.client.get(f"/reports/{self.sports_report.run_id}/").status_code, 404)
        self.assertEqual(
            self.client.get(f"/reports/{self.sports_report.run_id}/export/json/").status_code,
            404,
        )

        self.client.force_login(self.staff)
        history = self.client.get("/reports/?range=30d")
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, "bonus")
        self.assertContains(history, "sports_capital")

    def test_customer_report_exports_share_business_data_across_all_formats(self) -> None:
        self.client.force_login(self.staff)
        report = self.sports_report
        detail = self.client.get(f"/reports/{report.run_id}/")
        json_export = self.client.get(f"/reports/{report.run_id}/export/json/")
        csv_export = self.client.get(f"/reports/{report.run_id}/export/csv/")
        pdf_export = self.client.get(f"/reports/{report.run_id}/export/pdf/")

        payload = json.loads(json_export.content)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Northbridge v Riverside")
        self.assertNotContains(detail, "Workflow transitions")
        self.assertEqual(json_export.status_code, 200)
        self.assertEqual(payload["report_id"], str(report.run_id))
        self.assertEqual(payload["engine"], "sports_capital")
        self.assertEqual(payload["mode"], "simulation")
        self.assertEqual(payload["providers"]["primary"], "Bookmaker A")
        self.assertEqual(payload["profit_loss"], "12")
        self.assertNotIn("details", payload)

        content = csv_export.content.decode()
        self.assertEqual(csv_export.status_code, 200)
        self.assertIn("report_id," + str(report.run_id), content)
        self.assertIn("match,Northbridge v Riverside", content)
        self.assertIn("mode,simulation", content)
        self.assertNotIn("workflow-secret", content)
        self.assertEqual(pdf_export.status_code, 200)
        self.assertEqual(pdf_export["Content-Type"], "application/pdf")
        self.assertTrue(pdf_export.content.startswith(b"%PDF-1.4"))

    def test_missing_simulation_report_has_safe_admin_state(self) -> None:
        self.client.force_login(self.staff)

        missing = self.client.get(f"/reports/{uuid4()}/")

        self.assertEqual(missing.status_code, 404)
        self.assertContains(missing, "This report is not available.", status_code=404)

    def test_report_history_localizes_each_currency_kpi_without_combining_them(self) -> None:
        dollar_report = _report(SimulationEngine.BONUS, currency="USD")
        reporting_service = CustomerReportingService(
            _ReportStore((self.sports_report, dollar_report), ())
        )
        self.client.force_login(self.staff)

        with patch("qbet.web.views.CUSTOMER_REPORTING_SERVICE", reporting_service):
            response = self.client.get("/reports/?range=30d")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<h3>EUR</h3>", html=False)
        self.assertContains(response, "<h3>USD</h3>", html=False)
        self.assertContains(response, "50,00 EUR")
        self.assertContains(response, "50,00 USD")
        self.assertNotContains(response, "100,00 EUR")
        self.assertNotContains(response, "100,00 USD")
