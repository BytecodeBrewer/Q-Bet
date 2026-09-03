from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

django.setup()

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from qbet.layers import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import SimulationReport
from qbet.simulation import SimulationEngine, SimulationRunConfig, SimulationStatus
from qbet.web.models import SimulationAvailability
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
    engine: SimulationEngine, *, status: SimulationStatus = SimulationStatus.COMPLETED
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
        self.staff = User.objects.create_user(
            "staff", password="Strong-pass-123", is_staff=True
        )
        self.bonus_report = _report(
            SimulationEngine.BONUS, status=SimulationStatus.RUNNING
        )
        self.sports_report = _report(SimulationEngine.SPORTS_CAPITAL)
        self.service = MonitoringService(
            _ReportStore(
                (self.bonus_report, self.sports_report),
                _records(self.bonus_report) + _records(self.sports_report),
            )
        )
        self.monitoring_patch = patch("qbet.web.views.MONITORING_SERVICE", self.service)
        self.monitoring_patch.start()
        self.addCleanup(self.monitoring_patch.stop)

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

    def test_engine_detail_uses_read_model_for_activity_and_workflow_state(
        self,
    ) -> None:
        self.client.force_login(self.user)

        response = self.client.get("/engines/bonus/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Data aggregation")
        self.assertContains(response, "LiquidityChecker")
        self.assertContains(response, "Execution Layer state")
        self.assertContains(response, "All BonusEngine reports")

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
    def test_simulation_visibility_uses_persisted_runtime_control(self) -> None:
        SimulationAvailability.objects.update_or_create(
            pk=1,
            defaults={"enabled": False},
        )
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/simulation/").status_code, 404)
        self.assertEqual(self.client.get("/admin-area/gui-settings/").status_code, 302)

        self.client.force_login(self.staff)
        response = self.client.get("/admin-area/gui-settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Simulation availability is a persisted control-plane setting")
        self.assertEqual(self.client.post("/admin-area/gui-settings/").status_code, 405)

        SimulationAvailability.objects.filter(pk=1).update(enabled=True)
        self.client.force_login(self.user)
        response = self.client.get("/simulation/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode().count("data-simulation-engine="), 2)

    def test_presentation_preferences_are_session_scoped_and_disclosed(self) -> None:
        self.client.force_login(self.user)

        settings_response = self.client.get("/settings/presentation/")
        self.assertContains(settings_response, "browser session only")
        response = self.client.post(
            "/settings/presentation/", {"theme": "dark", "font_size": "large"}
        )
        self.assertRedirects(response, "/settings/presentation/")
        dashboard = self.client.get("/dashboard/")

        self.assertContains(dashboard, 'data-theme="dark"')
        self.assertContains(dashboard, 'data-font-size="large"')

    def test_report_exports_share_required_metadata_and_selected_sections(self) -> None:
        self.client.force_login(self.user)
        detail_url = f"/reports/{self.bonus_report.run_id}/"
        all_sections = (
            "include_events=1&include_intermediate_results=1&include_raw_inputs=1&"
            "include_warnings=1&include_errors=1&include_risk_decisions=1&"
            "include_workflow_transitions=1"
        )

        compact = self.client.get(detail_url)
        selected = self.client.get(f"{detail_url}?{all_sections}")
        json_export = self.client.get(
            f"/reports/{self.bonus_report.run_id}/export/json/?{all_sections}"
        )
        csv_export = self.client.get(
            f"/reports/{self.bonus_report.run_id}/export/csv/?{all_sections}"
        )

        self.assertEqual(compact.status_code, 200)
        self.assertNotIn("fixture", compact.content.decode())
        self.assertIn("fixture", selected.content.decode())

        payload = json.loads(json_export.content)
        self.assertEqual(json_export.status_code, 200)
        self.assertEqual(payload["run_id"], str(self.bonus_report.run_id))
        self.assertEqual(payload["engine"], "bonus")
        self.assertEqual(payload["mode"], "simulation")
        self.assertTrue(payload["generated_at"].endswith("+00:00"))
        self.assertEqual(
            set(payload["details"]),
            {
                "events",
                "intermediate_results",
                "raw_input_snapshots",
                "warnings",
                "errors",
                "risk_decisions",
                "workflow_transitions",
            },
        )
        self.assertEqual(
            payload["details"]["raw_input_snapshots"][0]["market"], "fixture"
        )
        self.assertEqual(
            payload["details"]["raw_input_snapshots"][0]["credential"],
            "raw-input-secret",
        )

        content = csv_export.content.decode()
        self.assertEqual(csv_export.status_code, 200)
        self.assertIn("run_id," + str(self.bonus_report.run_id), content)
        self.assertIn("engine,bonus", content)
        self.assertIn("mode,simulation", content)
        self.assertIn(f"generated_at,{payload['generated_at']}", content)
        for field in payload["details"]:
            self.assertIn(f"detail.{field}", content)

    def test_report_exports_redact_non_raw_sensitive_log_payloads(self) -> None:
        self.client.force_login(self.user)
        selection = (
            "include_warnings=1&include_errors=1&include_risk_decisions=1&"
            "include_workflow_transitions=1"
        )
        json_export = self.client.get(
            f"/reports/{self.bonus_report.run_id}/export/json/?{selection}"
        )
        csv_export = self.client.get(
            f"/reports/{self.bonus_report.run_id}/export/csv/?{selection}"
        )

        json_content = json_export.content.decode()
        csv_content = csv_export.content.decode()
        for sensitive_value in (
            "workflow-secret",
            "warning-credential",
            "warning-account",
            "error-token",
            "risk-account",
        ):
            self.assertNotIn(sensitive_value, json_content)
            self.assertNotIn(sensitive_value, csv_content)
        for sensitive_key in (
            "secret",
            "credential",
            "account_id",
            "token",
            "account_identifier",
        ):
            self.assertNotIn(sensitive_key, json_content)
            self.assertNotIn(sensitive_key, csv_content)

        payload = json.loads(json_content)
        self.assertEqual(
            payload["details"]["warnings"][0]["payload"],
            {"message": "safe warning"},
        )
        self.assertEqual(
            payload["details"]["workflow_transitions"][0]["payload"],
            {"stage": "data_aggregation", "decision": "allow"},
        )

    def test_report_history_and_missing_report_have_safe_states(self) -> None:
        self.client.force_login(self.user)

        history = self.client.get("/reports/")
        missing = self.client.get(f"/reports/{uuid4()}/")

        self.assertEqual(history.status_code, 200)
        self.assertContains(history, "bonus")
        self.assertContains(history, "sports_capital")
        self.assertEqual(missing.status_code, 404)
        self.assertContains(missing, "This report is not available.", status_code=404)
