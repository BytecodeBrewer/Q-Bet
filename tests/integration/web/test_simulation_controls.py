from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

django.setup()

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from qbet.layers import SimulationLogRecordType
from qbet.simulation import SimulationEngine
from qbet.storage import SQLiteSimulationReportReader
from qbet.web.models import SimulationAvailability, SimulationRunState
from qbet.web.monitoring import MonitoringService
from qbet.web.simulation_control import SimulationControlService
from qbet.workflow import WorkflowStage


class SimulationGuiControlTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("member-75", password="Strong-pass-123")
        self.staff = User.objects.create_user(
            "staff-75",
            password="Strong-pass-123",
            is_staff=True,
        )
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.report_db = Path(self.directory.name) / "simulation.sqlite3"
        SimulationAvailability.objects.all().delete()
        SimulationRunState.objects.all().delete()

    @override_settings(QBET_SIMULATION_MODE_ENABLED=False)
    def test_admin_can_enable_and_disable_persistent_simulation_availability(self) -> None:
        self.client.force_login(self.staff)

        enabled = self.client.post(
            "/admin-area/gui-settings/simulation/",
            {"enabled": "on"},
            follow=True,
        )
        state = SimulationAvailability.objects.get(pk=1)

        self.assertTrue(state.enabled)
        self.assertContains(enabled, "Simulation availability enabled.")

        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/simulation/").status_code, 200)

        self.client.force_login(self.staff)
        disabled = self.client.post(
            "/admin-area/gui-settings/simulation/",
            {},
            follow=True,
        )

        state.refresh_from_db()
        self.assertFalse(state.enabled)
        self.assertContains(disabled, "Simulation availability disabled.")

        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/simulation/").status_code, 404)

    def test_disable_is_blocked_while_any_simulation_run_is_active(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        run = SimulationRunState.objects.create(
            run_id=uuid4(),
            engine=SimulationEngine.BONUS.value,
            status=SimulationRunState.Status.RUNNING,
            progress=Decimal("0.5"),
            current_capital=Decimal("99.50"),
        )
        self.client.force_login(self.staff)

        blocked = self.client.post(
            "/admin-area/gui-settings/simulation/",
            {},
            follow=True,
        )

        self.assertTrue(SimulationAvailability.objects.get(pk=1).enabled)
        self.assertContains(
            blocked,
            "Simulation cannot be disabled while a run is active.",
        )

        run.status = SimulationRunState.Status.COMPLETED
        run.save(update_fields=("status", "updated_at"))
        allowed = self.client.post(
            "/admin-area/gui-settings/simulation/",
            {},
            follow=True,
        )

        self.assertFalse(SimulationAvailability.objects.get(pk=1).enabled)
        self.assertContains(allowed, "Simulation availability disabled.")

    def test_normal_user_cannot_change_global_simulation_availability(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=False)
        self.client.force_login(self.user)

        response = self.client.post(
            "/admin-area/gui-settings/simulation/",
            {"enabled": "on"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SimulationAvailability.objects.get(pk=1).enabled)

    @override_settings(QBET_SIMULATION_MODE_ENABLED=False)
    def test_disabled_simulation_rejects_start_without_creating_run(self) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.BONUS.value,
                "starting_capital": "100.00",
                "max_duration_minutes": "60",
            },
        )

        self.assertRedirects(response, "/dashboard/")
        self.assertFalse(SimulationRunState.objects.exists())

    def test_invalid_start_configuration_does_not_create_partial_state(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.user)

        response = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.BONUS.value,
                "starting_capital": "1.00",
                "max_duration_minutes": "9000",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(SimulationRunState.objects.exists())

    def test_gui_start_runs_connected_pipeline_and_persists_report_and_records(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        reader = SQLiteSimulationReportReader(self.report_db)
        monitoring = MonitoringService(reader)
        self.client.force_login(self.user)

        with (
            override_settings(QBET_SIMULATION_REPORT_DB=self.report_db),
            patch("qbet.web.views.MONITORING_SERVICE", monitoring),
        ):
            response = self.client.post(
                "/simulation/start/",
                {
                    "engine": SimulationEngine.BONUS.value,
                    "starting_capital": "100.00",
                    "max_duration_minutes": "60",
                },
                follow=True,
            )

            run = SimulationRunState.objects.get()
            self.assertIsNotNone(run.report_id)
            assert run.report_id is not None
            report = reader.load_report(run.report_id)
            records = reader.load_records(run.run_id)
            detail = self.client.get(f"/reports/{run.report_id}/")
            json_export = self.client.get(
                f"/reports/{run.report_id}/export/json/"
            )
            csv_export = self.client.get(
                f"/reports/{run.report_id}/export/csv/"
            )

        self.assertEqual(run.status, SimulationRunState.Status.COMPLETED)
        self.assertEqual(run.run_id, run.report_id)
        self.assertEqual(report.run_id, run.run_id)
        self.assertEqual(report.engine, SimulationEngine.BONUS.value)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(run.run_id))
        self.assertContains(response, "completed")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(json_export.status_code, 200)
        self.assertEqual(csv_export.status_code, 200)

        workflow_stages = {
            record.payload.get("stage")
            for record in records
            if record.record_type is SimulationLogRecordType.WORKFLOW_TRANSITION
        }
        self.assertIn(WorkflowStage.DOMAIN_RISK.value, workflow_stages)
        self.assertIn(WorkflowStage.LIQUIDITY_CHECK.value, workflow_stages)
        self.assertTrue(
            any(
                record.record_type is SimulationLogRecordType.RUN_FINISHED
                for record in records
            )
        )

    def test_control_service_supports_both_current_v1_engines(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        service = SimulationControlService()

        with override_settings(QBET_SIMULATION_REPORT_DB=self.report_db):
            bonus = service.start(
                engine=SimulationEngine.BONUS,
                starting_capital=Decimal("100"),
                max_duration=timedelta(minutes=60),
            )
            sports = service.start(
                engine=SimulationEngine.SPORTS_CAPITAL,
                starting_capital=Decimal("100"),
                max_duration=timedelta(minutes=60),
            )

        self.assertEqual(bonus.status, SimulationRunState.Status.COMPLETED)
        self.assertEqual(sports.status, SimulationRunState.Status.COMPLETED)
        self.assertEqual(
            set(SimulationRunState.objects.values_list("engine", flat=True)),
            {
                SimulationEngine.BONUS.value,
                SimulationEngine.SPORTS_CAPITAL.value,
            },
        )
