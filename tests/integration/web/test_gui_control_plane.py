from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

django.setup()

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.layers import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import SimulationReport
from qbet.simulation import SimulationEngine, SimulationRunConfig, SimulationStatus
from qbet.web.controls import GuiFeatureControlStore
from qbet.web.monitoring import MonitoringService


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

    def load_report(self, run_id):
        for report in self._reports:
            if report.run_id == run_id:
                return report
        raise KeyError(run_id)

    def load_records(self, run_id):
        records = tuple(record for record in self._records if record.run_id == run_id)
        if not records:
            raise KeyError(run_id)
        return records


def _report(engine: SimulationEngine, *, status: SimulationStatus = SimulationStatus.COMPLETED) -> SimulationReport:
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
    )


def _records(report: SimulationReport) -> tuple[SimulationLogRecord, ...]:
    return (
        SimulationLogRecord(
            run_id=report.run_id,
            sequence=1,
            timestamp=datetime(2026, 9, 2, tzinfo=UTC),
            record_type=SimulationLogRecordType.WORKFLOW_TRANSITION,
            source="workflow.orchestrator",
            payload={"stage": "data_aggregation", "decision": "allow"},
        ),
        SimulationLogRecord(
            run_id=report.run_id,
            sequence=2,
            timestamp=datetime(2026, 9, 2, tzinfo=UTC),
            record_type=SimulationLogRecordType.WARNING,
            source="workflow.orchestrator",
            payload={"message": "safe warning"},
        ),
        SimulationLogRecord(
            run_id=report.run_id,
            sequence=3,
            timestamp=datetime(2026, 9, 2, tzinfo=UTC),
            record_type=SimulationLogRecordType.RAW_INPUT,
            source="simulation.runner",
            payload={"credential": "[redacted]", "market": "fixture"},
        ),
    )


class GuiControlPlaneTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("member", password="Strong-pass-123")
        self.staff = User.objects.create_user(
            "staff", password="Strong-pass-123", is_staff=True
        )
        self.bonus_report = _report(SimulationEngine.BONUS, status=SimulationStatus.RUNNING)
        self.sports_report = _report(SimulationEngine.SPORTS_CAPITAL)
        self.service = MonitoringService(
            _ReportStore(
                (self.bonus_report, self.sports_report),
                _records(self.bonus_report) + _records(self.sports_report),
            )
        )
        self.feature_store = GuiFeatureControlStore()
        self.monitoring_patch = patch("qbet.web.views.MONITORING_SERVICE", self.service)
        self.feature_patch = patch("qbet.web.views.GUI_FEATURES", self.feature_store)
        self.monitoring_patch.start()
        self.feature_patch.start()
        self.addCleanup(self.monitoring_patch.stop)
        self.addCleanup(self.feature_patch.stop)

    def test_dashboard_is_protected_and_has_exactly_two_v1_engine_widgets(self) -> None:
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
        self.assertContains(response, "BonusEngine", count=1)
        self.assertContains(response, "SportsCapitalEngine", count=1)
        self.assertContains(response, "Recorded capital")
        self.assertContains(response, "live capital coverage is unavailable")
        self.assertNotContains(response, "BaseEngine")
        self.assertNotContains(response, "YieldEngine")
        self.assertNotContains(response, "AlphaEngine")

    def test_engine_detail_uses_read_model_for_activity_and_workflow_state(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get("/engines/bonus/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data aggregation")
        self.assertContains(response, "LiquidityChecker")
        self.assertContains(response, "Execution Layer state")
        self.assertContains(response, "All BonusEngine reports")

    def test_simulation_visibility_is_admin_controlled(self) -> None:
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/simulation/").status_code, 404)
        self.assertEqual(self.client.get("/admin-area/gui-settings/").status_code, 302)

        self.client.force_login(self.staff)
        response = self.client.post(
            "/admin-area/gui-settings/", {"simulation_enabled": "on"}
        )
        self.assertRedirects(response, "/admin-area/gui-settings/")

        self.client.force_login(self.user)
        response = self.client.get("/simulation/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count("data-simulation-engine="), 2)

    def test_presentation_preferences_are_session_persisted(self) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            "/settings/presentation/", {"theme": "dark", "font_size": "large"}
        )
        self.assertRedirects(response, "/settings/presentation/")
        dashboard = self.client.get("/dashboard/")

        self.assertContains(dashboard, 'data-theme="dark"')
        self.assertContains(dashboard, 'data-font-size="large"')

    def test_report_detail_hides_raw_data_by_default_and_exports_selected_data(self) -> None:
        self.client.force_login(self.user)
        detail_url = f"/reports/{self.bonus_report.run_id}/"

        compact = self.client.get(detail_url)
        selected = self.client.get(f"{detail_url}?include_raw_inputs=1&include_warnings=1")
        json_export = self.client.get(
            f"/reports/{self.bonus_report.run_id}/export/json/?include_raw_inputs=1"
        )
        csv_export = self.client.get(
            f"/reports/{self.bonus_report.run_id}/export/csv/?include_warnings=1"
        )

        self.assertEqual(compact.status_code, 200)
        self.assertNotIn("fixture", compact.content.decode())
        self.assertIn("fixture", selected.content.decode())
        self.assertEqual(json_export.status_code, 200)
        self.assertEqual(json.loads(json_export.content)["engine"], "bonus")
        self.assertEqual(csv_export.status_code, 200)
        self.assertIn("engine,bonus", csv_export.content.decode())
        self.assertIn("include_warnings", csv_export.content.decode())

    def test_report_history_and_missing_report_have_safe_states(self) -> None:
        self.client.force_login(self.user)

        history = self.client.get("/reports/")
        missing = self.client.get(f"/reports/{uuid4()}/")

        self.assertEqual(history.status_code, 200)
        self.assertContains(history, "bonus")
        self.assertContains(history, "sports_capital")
        self.assertEqual(missing.status_code, 404)
        self.assertContains(missing, "This report is not available.", status_code=404)