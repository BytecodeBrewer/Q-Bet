from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from qbet.data import TheOddsApiAdapter
from qbet.layers import SimulationLogRecordType
from qbet.simulation import SimulationEngine
from qbet.simulation.opportunity_source import (
    TheOddsApiSportsSimulationConfig,
    TheOddsApiSportsSimulationOpportunitySource,
)
from qbet.storage.ledger import PortfolioLedgerRepository, RoutingConfigurationRepository
from qbet.storage.models import ModeWorkQueueRow
from qbet.storage.postgres import PostgresSimulationReportReader
from qbet.web.models import SimulationAvailability, SimulationRunState, UserDisplayPreference
from qbet.web.monitoring import MonitoringService
from qbet.web.simulation_control import (
    SimulationAlreadyRunningError,
    SimulationControlError,
    SimulationControlService,
)
from qbet.workflow import WorkflowStage
from qbet.workflow.routing import EngineModes, RoutingConfiguration

django.setup()


class SimulationGuiControlTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("member-75", password="Strong-pass-123")
        self.staff = User.objects.create_user(
            "staff-75",
            password="Strong-pass-123",
            is_staff=True,
        )
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
        self.assertContains(enabled, "Admin Area")
        self.assertEqual(self.client.get("/simulation/").status_code, 200)

        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/simulation/").status_code, 404)

        self.client.force_login(self.staff)
        disabled = self.client.post(
            "/admin-area/gui-settings/simulation/",
            {},
            follow=True,
        )

        state.refresh_from_db()
        self.assertFalse(state.enabled)
        self.assertContains(disabled, "Simulation availability disabled.")
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

    def test_simulation_page_uses_persisted_running_state(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        RoutingConfigurationRepository().save(
            RoutingConfiguration(bonus=EngineModes(simulation=True))
        )
        SimulationRunState.objects.create(
            run_id=uuid4(),
            engine=SimulationEngine.BONUS.value,
            status=SimulationRunState.Status.RUNNING,
            progress=Decimal("0.25"),
            current_capital=Decimal("100"),
        )
        self.client.force_login(self.staff)

        response = self.client.get("/simulation/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<dt>Status</dt><dd>Running.</dd>", html=True)
        self.assertContains(response, "<dt>Running / pending</dt><dd>1 / 0</dd>", html=True)

    def test_simulation_surface_uses_display_preferences_without_fx_relabeling(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        RoutingConfigurationRepository().save(
            RoutingConfiguration(bonus=EngineModes(simulation=True))
        )
        SimulationRunState.objects.create(
            run_id=uuid4(),
            engine=SimulationEngine.BONUS.value,
            status=SimulationRunState.Status.RUNNING,
            progress=Decimal("0.25"),
            current_capital=Decimal("100"),
        )
        UserDisplayPreference.objects.create(
            user=self.staff,
            language="de",
            region="DE",
            timezone_name="Europe/Berlin",
            time_format="24h",
            currency="USD",
        )
        self.client.force_login(self.staff)

        response = self.client.get("/simulation/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100,00 EUR")
        self.assertContains(response, "Preferred report currency: USD.")
        self.assertContains(response, "no FX conversion is applied.")
        self.assertContains(response, "Running / pending")
        self.assertNotContains(response, "Active / pending")

    def test_normal_user_cannot_change_global_simulation_availability(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=False)
        self.client.force_login(self.user)

        response = self.client.post(
            "/admin-area/gui-settings/simulation/",
            {"enabled": "on"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SimulationAvailability.objects.get(pk=1).enabled)

    def test_normal_user_cannot_start_simulation_even_when_enabled(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.user)

        response = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.BONUS.value,
                "starting_capital": "100.00",
                "max_duration_minutes": "60",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(SimulationRunState.objects.exists())

    @override_settings(QBET_SIMULATION_MODE_ENABLED=False)
    def test_disabled_simulation_rejects_staff_start_without_creating_run(self) -> None:
        self.client.force_login(self.staff)

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
        self.client.force_login(self.staff)

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

    def test_duplicate_start_for_same_active_engine_is_rejected(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        active_run = SimulationRunState.objects.create(
            run_id=uuid4(),
            engine=SimulationEngine.BONUS.value,
            status=SimulationRunState.Status.RUNNING,
            progress=Decimal("0.25"),
            current_capital=Decimal("100"),
        )
        service = SimulationControlService()

        with self.assertRaises(SimulationAlreadyRunningError):
            service.start(
                engine=SimulationEngine.BONUS,
                starting_capital=Decimal("100"),
                max_duration=timedelta(minutes=60),
            )

        self.assertEqual(SimulationRunState.objects.count(), 1)
        self.assertTrue(SimulationRunState.objects.filter(pk=active_run.run_id).exists())

    def test_gui_simulation_exposes_running_state_then_persists_report_and_records(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        reader = PostgresSimulationReportReader()
        monitoring = MonitoringService(reader)
        self.client.force_login(self.staff)

        blocked = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.BONUS.value,
                "starting_capital": "100.00",
            },
            follow=True,
        )
        self.assertContains(blocked, "Enable this engine for Simulation before starting a simulation.")
        self.assertFalse(SimulationRunState.objects.exists())

        RoutingConfigurationRepository().save(
            RoutingConfiguration(bonus=EngineModes(simulation=True))
        )

        started = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.BONUS.value,
                "starting_capital": "100.00",
            },
        )
        run = SimulationRunState.objects.get()
        self.assertEqual(run.status, SimulationRunState.Status.RUNNING)
        self.assertRedirects(
            started,
            f"/simulation/?autostart={run.run_id}",
            fetch_redirect_response=False,
        )

        with patch("qbet.web.views.MONITORING_SERVICE", monitoring):
            running_page = self.client.get(f"/simulation/?autostart={run.run_id}")
            response = self.client.post(f"/simulation/{run.run_id}/run/")
            run.refresh_from_db()

            self.assertIsNotNone(run.report_id)
            assert run.report_id is not None
            report = reader.load_report(run.report_id)
            records = reader.load_records(run.run_id)
            detail = self.client.get(f"/reports/{run.report_id}/")
            json_export = self.client.get(f"/reports/{run.report_id}/export/json/")
            csv_export = self.client.get(f"/reports/{run.report_id}/export/csv/")

        self.assertContains(running_page, "Run is running.")
        self.assertContains(running_page, "Stop simulation")
        self.assertContains(running_page, f'/simulation/{run.run_id}/run/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], SimulationRunState.Status.COMPLETED)
        self.assertEqual(run.status, SimulationRunState.Status.COMPLETED)
        self.assertEqual(run.run_id, run.report_id)
        self.assertEqual(report.run_id, run.run_id)
        self.assertEqual(report.engine, SimulationEngine.BONUS.value)
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
            any(record.record_type is SimulationLogRecordType.RUN_FINISHED for record in records)
        )

    def test_gui_stop_transitions_active_run_without_running_work(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        RoutingConfigurationRepository().save(
            RoutingConfiguration(bonus=EngineModes(simulation=True))
        )
        self.client.force_login(self.staff)

        self.client.post(
            "/simulation/start/",
            {"engine": SimulationEngine.BONUS.value, "starting_capital": "100.00"},
        )
        run = SimulationRunState.objects.get()

        stopped = self.client.post(f"/simulation/{run.run_id}/stop/")
        run.refresh_from_db()

        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json()["status"], SimulationRunState.Status.STOPPED)
        self.assertEqual(run.status, SimulationRunState.Status.STOPPED)
        self.assertIsNone(run.report_id)

    def test_pipeline_dry_run_is_separate_and_has_no_business_side_effects(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.staff)

        response = self.client.post(
            "/simulation/pipeline-dry-run/",
            {"engine": SimulationEngine.BONUS.value, "mode": "simulation"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pipeline dry-run")
        self.assertContains(response, "Readiness only")
        self.assertContains(
            response,
            "without provider, opportunity, order, or capital side effects",
        )
        self.assertFalse(SimulationRunState.objects.exists())
        self.assertFalse(ModeWorkQueueRow.objects.exists())
        self.assertIsNone(
            PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        )

    def test_routing_and_simulation_state_survive_service_recreation(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        expected_routing = RoutingConfiguration(
            bonus=EngineModes(simulation=True)
        )
        RoutingConfigurationRepository().save(expected_routing)

        run = SimulationControlService().start(
            engine=SimulationEngine.BONUS,
            starting_capital=Decimal("100"),
            max_duration=timedelta(minutes=60),
        )

        restored_routing = RoutingConfigurationRepository().load()
        self.assertEqual(restored_routing, expected_routing)

        restored_control = SimulationControlService().snapshot()
        restored_run = next(item for item in restored_control.runs if item.run_id == run.run_id)
        self.assertEqual(restored_run.status, SimulationRunState.Status.COMPLETED)
        self.assertEqual(restored_run.report_id, run.report_id)

        reader = PostgresSimulationReportReader()
        assert run.report_id is not None
        restored_report = reader.load_report(run.report_id)
        self.assertEqual(restored_report.run_id, run.run_id)

        monitoring = MonitoringService(reader).snapshot(
            runtime_configuration=restored_routing
        )
        bonus = next(engine for engine in monitoring.engines if engine.engine_id == "bonus")
        self.assertEqual(bonus.status, "gray")
        self.assertEqual(bonus.live_state, "ready")
        self.assertEqual(bonus.total_activity, 1)

    def test_control_service_supports_both_current_v1_engines(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        service = SimulationControlService()

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

    def test_connected_sports_simulation_reads_one_market_and_persists_run_correlation(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        requested_urls: list[str] = []
        now = datetime(2026, 9, 19, 1, 0, tzinfo=UTC)
        provider_payload = {
            "id": "event-123",
            "sport_key": "tennis_atp",
            "bookmakers": [
                {
                    "key": "book-a",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Home", "price": 2.4},
                                {"name": "Away", "price": 2.2},
                            ],
                        }
                    ],
                },
                {
                    "key": "book-b",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Home", "price": 2.3},
                                {"name": "Away", "price": 2.5},
                            ],
                        }
                    ],
                },
            ],
        }
        adapter = TheOddsApiAdapter(
            api_key="configured-for-test",
            available_stake=Decimal("100"),
            clock=lambda: now,
            http_get=lambda url: (
                requested_urls.append(url) or 200,
                {},
                json.dumps(provider_payload).encode(),
            ),
        )
        source = TheOddsApiSportsSimulationOpportunitySource(
            TheOddsApiSportsSimulationConfig(
                sport="tennis_atp",
                event_id="event-123",
                market="h2h",
                assumed_liquidity=Decimal("100"),
                requested_total_stake=Decimal("20"),
                stake_precision=Decimal("0.01"),
            ),
            collector=adapter,
        )
        service = SimulationControlService(opportunity_source=source)

        run = service.start(
            engine=SimulationEngine.SPORTS_CAPITAL,
            starting_capital=Decimal("100"),
            max_duration=timedelta(minutes=60),
        )

        self.assertEqual(run.status, SimulationRunState.Status.COMPLETED)
        self.assertEqual(len(requested_urls), 1)
        self.assertIn("/sports/tennis_atp/events/event-123/odds?", requested_urls[0])
        self.assertIsNotNone(run.report_id)
        assert run.report_id is not None
        report = PostgresSimulationReportReader().load_report(run.report_id)
        self.assertEqual(report.run_id, run.run_id)
        self.assertIsNotNone(report.customer_report_input)
        assert report.customer_report_input is not None
        self.assertEqual(report.customer_report_input.transaction_id, str(run.run_id))
        self.assertEqual(
            {
                report.customer_report_input.provider,
                report.customer_report_input.counterparty_provider,
            },
            {"book-a", "book-b"},
        )
        ledger = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        self.assertIsNotNone(ledger)
        assert ledger is not None
        self.assertTrue(ledger.commands)
        self.assertEqual(
            {command.correlation_id for command in ledger.commands.values()},
            {str(run.run_id)},
        )

    @override_settings(
        QBET_SIMULATION_SPORTS_SOURCE="the_odds_api",
        QBET_SIMULATION_ODDS_SPORT="",
        QBET_SIMULATION_ODDS_EVENT_ID="",
        QBET_SIMULATION_ODDS_MARKET="",
        QBET_SIMULATION_ASSUMED_LIQUIDITY="",
        QBET_SIMULATION_REQUESTED_TOTAL_STAKE="",
        QBET_SIMULATION_STAKE_PRECISION="",
    )
    def test_connected_sports_configuration_failure_is_safe_and_persisted(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        service = SimulationControlService()

        with self.assertRaises(SimulationControlError) as raised:
            service.start(
                engine=SimulationEngine.SPORTS_CAPITAL,
                starting_capital=Decimal("100"),
                max_duration=timedelta(minutes=60),
            )

        self.assertEqual(
            raised.exception.reason_code,
            "simulation_market_configuration_missing",
        )
        run = SimulationRunState.objects.get()
        self.assertEqual(run.status, SimulationRunState.Status.FAILED)
        self.assertEqual(
            run.error_message,
            "simulation_market_configuration_missing",
        )

