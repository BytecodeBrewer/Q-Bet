from __future__ import annotations

import json
import os
from threading import Event, Thread
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

from django.contrib.auth.models import User
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings

from qbet.data import TheOddsApiAdapter
from qbet.domain.ledger import LedgerCommand, LedgerOperation, PortfolioBalance
from qbet.layers import SimulationLogRecordType
from qbet.ledger import PortfolioLedger
from qbet.simulation import SimulationEngine
from qbet.simulation.opportunity_source import (
    DeterministicSimulationOpportunitySource,
    SimulationOpportunityBundle,
    TheOddsApiSportsSimulationConfig,
    TheOddsApiSportsSimulationOpportunitySource,
)
from qbet.storage.ledger import PortfolioLedgerRepository, RoutingConfigurationRepository
from qbet.storage.models import ModeWorkQueueRow, PortfolioLedgerRow
from qbet.storage.postgres import PostgresSimulationReportReader
from qbet.web.models import (
    SimulationAvailability,
    SimulationPortfolioResetArchive,
    SimulationPortfolioState,
    SimulationRunState,
    UserDisplayPreference,
)
from qbet.web.monitoring import MonitoringService
from qbet.web.simulation_control import (
    SimulationAlreadyRunningError,
    SimulationControlError,
    SimulationControlService,
)
from qbet.workflow import WorkflowMode, WorkflowStage
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.routing import EngineModes, RoutingConfiguration
from tests.support.workflow import bonus_request

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
        SimulationControlService().seed_portfolio(amount=Decimal("100"), currency="EUR")

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
        self.assertContains(response, "<dt>Status</dt><dd>Inactive.</dd>", html=True)
        self.assertContains(response, "<dt>Running / pending</dt><dd>1 / 0</dd>", html=True)
        self.assertContains(
            response,
            "BonusEngine combines your active promotion terms with fresh fixed-odds sportsbook data.",
        )

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
        self.assertContains(response, "Preferred recorded currency: USD.")
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

    def test_product_simulation_form_exposes_connected_bonus_and_sports_capital(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.staff)

        response = self.client.get("/simulation/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<option value="sports_capital">SportsCapitalEngine</option>',
            count=2,
            html=True,
        )
        self.assertContains(
            response,
            '<option value="bonus">BonusEngine</option>',
            count=2,
            html=True,
        )
        self.assertContains(response, "Pipeline dry-run")
        self.assertContains(
            response,
            "BonusEngine combines your active promotion terms with fresh fixed-odds sportsbook data.",
        )
        self.assertContains(
            response,
            "BonusEngine combines your active promotion terms with fresh fixed-odds sportsbook data.",
        )

    def test_bonus_gui_start_records_authenticated_run_owner(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        RoutingConfigurationRepository().save(
            RoutingConfiguration(bonus=EngineModes(simulation=True))
        )
        self.client.force_login(self.staff)

        response = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.BONUS.value,
                "currency": "EUR",
                "max_duration_minutes": "60",
            },
        )

        self.assertEqual(response.status_code, 302)
        run = SimulationRunState.objects.get()
        self.assertEqual(run.engine, SimulationEngine.BONUS.value)
        self.assertEqual(run.initiated_by, self.staff)


    def test_normal_user_cannot_start_simulation_even_when_enabled(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.user)

        response = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.BONUS.value,
                "currency": "EUR",
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
                "currency": "EUR",
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
                "currency": "JPY",
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
                currency="EUR",
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
                "engine": SimulationEngine.SPORTS_CAPITAL.value,
                "currency": "EUR",
            },
            follow=True,
        )
        self.assertContains(blocked, "Enable this engine for Simulation before starting a simulation.")
        self.assertFalse(SimulationRunState.objects.exists())

        RoutingConfigurationRepository().save(
            RoutingConfiguration(sports_capital=EngineModes(simulation=True))
        )

        started = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.SPORTS_CAPITAL.value,
                "currency": "EUR",
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
        self.assertEqual(report.engine, SimulationEngine.SPORTS_CAPITAL.value)
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
            RoutingConfiguration(sports_capital=EngineModes(simulation=True))
        )
        self.client.force_login(self.staff)

        self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.SPORTS_CAPITAL.value,
                "currency": "EUR",
            },
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
        ledger = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        self.assertIsNotNone(ledger)
        assert ledger is not None
        self.assertEqual(ledger.balance.available, Decimal("100"))
        self.assertFalse(ledger.commands)

    def test_simulation_portfolio_tables_are_protected_from_supabase_api_roles(self) -> None:
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL RLS assertion")

        tables = (
            "qbet_simulation_portfolio_state",
            "qbet_simulation_portfolio_reset_archives",
        )
        with connection.cursor() as cursor:
            for table in tables:
                cursor.execute(
                    "SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass",
                    [f"public.{table}"],
                )
                self.assertTrue(cursor.fetchone()[0], f"{table} must have RLS enabled")

                for role in ("anon", "authenticated"):
                    cursor.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = %s)",
                        [role],
                    )
                    if not cursor.fetchone()[0]:
                        continue
                    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                        cursor.execute(
                            "SELECT has_table_privilege(%s, %s, %s)",
                            [role, f"public.{table}", privilege],
                        )
                        self.assertFalse(
                            cursor.fetchone()[0],
                            f"{role} retains {privilege} on {table}",
                        )

    def test_portfolio_seed_is_staff_controlled_and_cannot_be_repeated(self) -> None:
        SimulationPortfolioState.objects.all().delete()
        PortfolioLedgerRow.objects.filter(mode="simulation").delete()
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.staff)

        response = self.client.post(
            "/simulation/portfolio/seed/",
            {"amount": "1000.00", "currency": "EUR", "return_to": "simulation"},
        )

        self.assertRedirects(response, "/simulation/")
        state = SimulationPortfolioState.objects.get(pk=1)
        self.assertEqual(state.seed_balances, {"EUR": "1000.00"})
        seeded_ledger = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        assert seeded_ledger is not None
        self.assertEqual(seeded_ledger.balance.available, Decimal("1000.00"))
        repeated = self.client.post(
            "/simulation/portfolio/seed/",
            {"amount": "500.00", "currency": "EUR"},
            follow=True,
        )
        self.assertContains(repeated, "already initialized")
        self.assertEqual(SimulationPortfolioResetArchive.objects.count(), 0)

    def test_portfolio_reset_requires_confirmation_and_archives_simulation_only(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        simulation = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        assert simulation is not None
        changed, decision = simulation.apply(
            LedgerCommand(
                id="test-cost",
                dispatch_id="test-cost",
                correlation_id="test-reset",
                currency="EUR",
                operation=LedgerOperation.COST,
                amount=Decimal("5"),
            )
        )
        self.assertTrue(decision.accepted)
        PortfolioLedgerRepository().save(changed)
        PortfolioLedgerRow.objects.create(
            mode="execution",
            currency="EUR",
            payload=PortfolioLedger(
                balance=PortfolioBalance(
                    mode="execution", currency="EUR", available=Decimal("250")
                )
            ).model_dump(mode="json"),
        )
        self.client.force_login(self.staff)

        unconfirmed = self.client.post("/simulation/portfolio/reset/", {})
        self.assertRedirects(unconfirmed, "/dashboard/")
        self.assertEqual(SimulationPortfolioResetArchive.objects.count(), 0)

        confirmed = self.client.post(
            "/simulation/portfolio/reset/", {"confirm_reset": "on"}
        )

        self.assertRedirects(confirmed, "/dashboard/")
        reset = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        execution = PortfolioLedgerRepository().load(mode="execution", currency="EUR")
        assert reset is not None and execution is not None
        self.assertEqual(reset.balance.available, Decimal("100"))
        self.assertFalse(reset.commands)
        self.assertEqual(execution.balance.available, Decimal("250"))
        self.assertEqual(SimulationPortfolioResetArchive.objects.count(), 1)
        archive = SimulationPortfolioResetArchive.objects.get()
        self.assertIn("test-cost", archive.portfolio_payload["ledgers"]["EUR"]["commands"])

    def test_portfolio_reset_is_blocked_while_simulation_run_is_active(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        SimulationRunState.objects.create(
            run_id=uuid4(),
            engine=SimulationEngine.BONUS.value,
            status=SimulationRunState.Status.RUNNING,
        )
        self.client.force_login(self.staff)

        response = self.client.post(
            "/simulation/portfolio/reset/", {"confirm_reset": "on"}, follow=True
        )

        self.assertContains(response, "Stop or finish active Simulation runs")
        self.assertEqual(SimulationPortfolioResetArchive.objects.count(), 0)
        ledger = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        assert ledger is not None
        self.assertEqual(ledger.balance.available, Decimal("100"))

    def test_portfolio_reset_is_blocked_while_simulation_queue_has_work(self) -> None:
        now = datetime.now(UTC)
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(bonus=EngineModes(simulation=True))
        )
        coordinator.schedule(
            bonus_request("reset-queued-work", generated_at=now),
            owner=self.staff.get_username(),
            correlation_id=uuid4(),
            scheduled_for=now,
            expires_at=now + timedelta(minutes=5),
        )
        self.client.force_login(self.staff)

        response = self.client.post(
            "/simulation/portfolio/reset/",
            {"confirm_reset": "on"},
            follow=True,
        )

        self.assertContains(response, "Finish or cancel queued Simulation work")
        self.assertEqual(SimulationPortfolioResetArchive.objects.count(), 0)
        self.assertTrue(
            ModeWorkQueueRow.objects.filter(mode="simulation", state="pending").exists()
        )

    def test_portfolio_reset_is_blocked_while_simulation_capital_is_unsettled(self) -> None:
        ledger = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        assert ledger is not None
        reserved, decision = ledger.apply(
            LedgerCommand(
                id="reset-reserved",
                dispatch_id="reset-reserved",
                correlation_id="reset-reserved",
                currency="EUR",
                operation=LedgerOperation.RESERVE,
                amount=Decimal("10"),
            )
        )
        self.assertTrue(decision.accepted)
        PortfolioLedgerRepository().save(reserved)
        self.client.force_login(self.staff)

        response = self.client.post(
            "/simulation/portfolio/reset/",
            {"confirm_reset": "on"},
            follow=True,
        )

        self.assertContains(response, "Settle or release all working Simulation capital")
        self.assertEqual(SimulationPortfolioResetArchive.objects.count(), 0)
        persisted = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        assert persisted is not None
        self.assertEqual(persisted.balance.reserved, Decimal("10"))

    def test_engines_consume_the_same_currency_ledger(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        service = SimulationControlService(
            opportunity_source=DeterministicSimulationOpportunitySource()
        )

        bonus_run = service.start(engine=SimulationEngine.BONUS, currency="EUR")
        after_bonus = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        assert after_bonus is not None
        self.assertTrue(after_bonus.commands)

        sports_pending = service.begin(
            engine=SimulationEngine.SPORTS_CAPITAL,
            currency="EUR",
        )
        self.assertEqual(sports_pending.current_capital, after_bonus.balance.available)
        sports_run = service.run(sports_pending.run_id)
        after_sports = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        assert after_sports is not None

        self.assertEqual(bonus_run.portfolio_currency, sports_run.portfolio_currency)
        self.assertEqual(
            PortfolioLedgerRow.objects.filter(mode="simulation").count(),
            1,
        )
        correlations = {
            command.correlation_id for command in after_sports.commands.values()
        }
        self.assertIn(str(bonus_run.run_id), correlations)
        self.assertIn(str(sports_run.run_id), correlations)
        self.assertGreater(len(after_sports.commands), len(after_bonus.commands))
        self.assertEqual(sports_run.current_capital, after_sports.balance.available)
        self.assertEqual(
            SimulationPortfolioState.objects.get(pk=1).seed_balances,
            {"EUR": "100"},
        )

    def test_engine_pause_does_not_mutate_shared_sandbox_capital(self) -> None:
        before = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        assert before is not None
        repository = RoutingConfigurationRepository()
        repository.save(
            RoutingConfiguration(
                bonus=EngineModes(simulation=True),
                sports_capital=EngineModes(simulation=True),
            )
        )

        updated = repository.set_mode_active(
            engine="bonus",
            mode=WorkflowMode.SIMULATION,
            active=False,
        )

        after = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        self.assertFalse(updated.bonus.simulation)
        self.assertTrue(updated.sports_capital.simulation)
        self.assertEqual(after, before)


    def test_routing_and_simulation_state_survive_service_recreation(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        expected_routing = RoutingConfiguration(
            sports_capital=EngineModes(simulation=True)
        )
        RoutingConfigurationRepository().save(expected_routing)

        run = SimulationControlService().start(
            engine=SimulationEngine.SPORTS_CAPITAL,
            currency="EUR",
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
        sports = next(
            engine for engine in monitoring.engines
            if engine.engine_id == "sports_capital"
        )
        self.assertEqual(sports.status, "gray")
        self.assertEqual(sports.live_state, "ready")
        self.assertEqual(sports.total_activity, 1)

    def test_default_bonus_simulation_fails_closed_without_run_owner(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        service = SimulationControlService()

        with self.assertRaises(SimulationControlError) as raised:
            service.start(
                engine=SimulationEngine.BONUS,
                currency="EUR",
                max_duration=timedelta(minutes=60),
            )

        self.assertEqual(
            raised.exception.reason_code,
            "bonus_offer_user_missing",
        )
        run = SimulationRunState.objects.get()
        self.assertEqual(run.status, SimulationRunState.Status.FAILED)
        self.assertEqual(run.error_message, "bonus_offer_user_missing")

    def test_control_service_runs_current_sports_capital_engine(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        run = SimulationControlService().start(
            engine=SimulationEngine.SPORTS_CAPITAL,
            currency="EUR",
            max_duration=timedelta(minutes=60),
        )

        self.assertEqual(run.status, SimulationRunState.Status.COMPLETED)
        self.assertEqual(
            set(SimulationRunState.objects.values_list("engine", flat=True)),
            {SimulationEngine.SPORTS_CAPITAL.value},
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
            currency="EUR",
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
                currency="EUR",
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


class _BlockingOpportunitySource:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.calls = 0
        self.delegate = DeterministicSimulationOpportunitySource()

    def build(self, config, correlation_id) -> SimulationOpportunityBundle:
        self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=10):
            raise TimeoutError("test did not release blocked simulation source")
        return self.delegate.build(config, correlation_id)


class SimulationRunConcurrencyTests(TransactionTestCase):
    def test_overlapping_run_requests_claim_and_execute_the_simulation_once(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        SimulationControlService().seed_portfolio(amount=Decimal("100"), currency="EUR")
        source = _BlockingOpportunitySource()
        service = SimulationControlService(opportunity_source=source)
        run = service.begin(
            engine=SimulationEngine.BONUS,
            currency="EUR",
        )
        worker_errors: list[BaseException] = []
        worker = Thread(
            target=self._run_in_thread,
            args=(service, run.run_id, worker_errors),
        )
        worker.start()

        self.assertTrue(source.started.wait(timeout=10))
        self.assertEqual(
            SimulationRunState.objects.get(pk=run.run_id).status,
            SimulationRunState.Status.PROCESSING,
        )
        with self.assertRaises(SimulationAlreadyRunningError):
            service.run(run.run_id)

        source.release.set()
        worker.join(timeout=15)

        self.assertFalse(worker.is_alive())
        self.assertEqual(worker_errors, [])
        self.assertEqual(source.calls, 1)
        persisted = SimulationRunState.objects.get(pk=run.run_id)
        self.assertEqual(persisted.status, SimulationRunState.Status.COMPLETED)
        self.assertIsNotNone(persisted.report_id)

    @staticmethod
    def _run_in_thread(service, run_id, errors: list[BaseException]) -> None:
        close_old_connections()
        try:
            service.run(run_id)
        except BaseException as error:
            errors.append(error)
        finally:
            close_old_connections()
