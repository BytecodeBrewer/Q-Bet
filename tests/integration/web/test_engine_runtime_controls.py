from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone

from qbet.storage.ledger import RoutingConfigurationRepository
from qbet.storage.models import ExecutionRecordRow, ModeWorkQueueRow, PortfolioLedgerRow
from qbet.web.models import SimulationAvailability


class EngineRuntimeControlTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            "runtime-user",
            password="Strong-pass-123",
        )
        self.staff = User.objects.create_user(
            "runtime-staff",
            password="Strong-pass-123",
            is_staff=True,
        )

    def test_staff_execution_start_and_stop_only_toggle_requested_engine_mode(self) -> None:
        self.client.force_login(self.staff)

        started = self.client.post("/engines/bonus/execution/start/")
        self.assertRedirects(started, "/dashboard/")
        configuration = RoutingConfigurationRepository().load()
        assert configuration is not None
        self.assertTrue(configuration.bonus.execution)
        self.assertTrue(configuration.bonus.execution_sandbox)
        self.assertFalse(configuration.bonus.simulation)
        self.assertFalse(configuration.sports_capital.execution)
        self.assertEqual(ModeWorkQueueRow.objects.count(), 0)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)

        dashboard = self.client.get("/dashboard/")
        self.assertContains(dashboard, 'aria-label="Disable BonusEngine execution"')
        self.assertContains(dashboard, 'title="Active"')

        stopped = self.client.post("/engines/bonus/execution/stop/")
        self.assertRedirects(stopped, "/dashboard/")
        configuration = RoutingConfigurationRepository().load()
        assert configuration is not None
        self.assertFalse(configuration.bonus.execution)
        self.assertEqual(ModeWorkQueueRow.objects.count(), 0)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)

        dashboard = self.client.get("/dashboard/")
        self.assertContains(dashboard, 'aria-label="Enable BonusEngine execution"')
        self.assertContains(dashboard, 'title="Inactive"')

    def test_enabled_execution_is_ready_until_durable_work_is_processing(self) -> None:
        self.client.force_login(self.staff)
        self.client.post("/engines/bonus/execution/start/")

        ready = self.client.get("/dashboard/")
        self.assertContains(ready, 'aria-label="Disable BonusEngine execution"')
        self.assertContains(ready, 'title="Active"')
        self.assertContains(ready, "Execution active")
        self.assertNotContains(ready, 'title="Running"')

        ModeWorkQueueRow.objects.create(
            work_id=uuid4(),
            correlation_id=uuid4(),
            mode="execution",
            state="processing",
            scheduled_for=timezone.now(),
            payload={"work": {"engine": "bonus"}},
        )

        running = self.client.get("/dashboard/")
        self.assertContains(running, 'title="Running"')
        self.assertContains(running, "Execution running")
        self.assertContains(running, "State <strong>running</strong>", html=False)

    def test_execution_activity_database_failure_is_fail_closed(self) -> None:
        class LazyDatabaseFailure:
            def values(self, *args: str):
                return self

            def __iter__(self):
                raise DatabaseError("work queue unavailable")

        self.client.force_login(self.staff)
        self.client.post("/engines/bonus/execution/start/")

        with patch(
            "qbet.web.views.ModeWorkQueueRow.objects.filter",
            return_value=LazyDatabaseFailure(),
        ):
            response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'title="Error"')
        self.assertContains(response, "State <strong>error</strong>", html=False)
        self.assertNotContains(response, "Execution running")

    def test_engine_detail_uses_durable_execution_running_state(self) -> None:
        self.client.force_login(self.staff)
        self.client.post("/engines/bonus/execution/start/")
        ModeWorkQueueRow.objects.create(
            work_id=uuid4(),
            correlation_id=uuid4(),
            mode="execution",
            state="processing",
            scheduled_for=timezone.now(),
            payload={"work": {"engine": "bonus"}},
        )

        response = self.client.get("/engines/bonus/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<dt>Operational state</dt><dd>Running</dd>", html=True)
        self.assertContains(response, "<dt>Running matches</dt><dd>1</dd>", html=True)
        self.assertContains(response, "Running.")

    def test_normal_user_cannot_toggle_execution_runtime_or_see_controls(self) -> None:
        self.client.force_login(self.user)

        dashboard = self.client.get("/dashboard/")
        self.assertNotContains(dashboard, 'aria-label="Enable BonusEngine execution"')
        self.assertNotContains(dashboard, 'aria-label="Disable BonusEngine execution"')

        response = self.client.post("/engines/bonus/execution/start/")

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(RoutingConfigurationRepository().load())
        self.assertEqual(ModeWorkQueueRow.objects.count(), 0)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)

    def test_normal_user_cannot_toggle_simulation_runtime(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.user)

        response = self.client.post("/engines/bonus/simulation/start/")

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(RoutingConfigurationRepository().load())

    def test_staff_simulation_toggle_preserves_execution_sibling_mode(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.staff)
        self.client.post("/engines/bonus/execution/start/")

        started = self.client.post("/engines/bonus/simulation/start/")
        self.assertRedirects(started, "/dashboard/")
        configuration = RoutingConfigurationRepository().load()
        assert configuration is not None
        self.assertTrue(configuration.bonus.execution)
        self.assertTrue(configuration.bonus.execution_sandbox)
        self.assertTrue(configuration.bonus.simulation)

        dashboard = self.client.get("/dashboard/")
        self.assertContains(dashboard, 'aria-label="Disable BonusEngine simulation"')
        self.assertContains(dashboard, "Deterministic pipeline test")
        self.assertContains(dashboard, "Run pipeline test")

        monitoring = self.client.get("/monitoring/")
        self.assertContains(monitoring, 'id="runtime-readiness-heading"')
        self.assertContains(monitoring, 'data-runtime-engine="execution:bonus"')
        self.assertContains(monitoring, 'data-runtime-engine="simulation:bonus"')
        self.assertContains(monitoring, "Data aggregation")
        self.assertContains(monitoring, "LiquidityChecker")

        stopped = self.client.post("/engines/bonus/simulation/stop/")
        self.assertRedirects(stopped, "/dashboard/")
        configuration = RoutingConfigurationRepository().load()
        assert configuration is not None
        self.assertTrue(configuration.bonus.execution)
        self.assertTrue(configuration.bonus.execution_sandbox)
        self.assertFalse(configuration.bonus.simulation)

    def test_staff_cannot_start_simulation_engine_when_simulation_layer_is_disabled(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=False)
        self.client.force_login(self.staff)

        response = self.client.post("/engines/bonus/simulation/start/", follow=True)

        self.assertContains(response, "Simulation is disabled.")
        configuration = RoutingConfigurationRepository().load()
        self.assertTrue(configuration is None or not configuration.bonus.simulation)

    def test_sandbox_execution_control_is_staff_only(self) -> None:
        self.client.force_login(self.user)
        denied = self.client.post("/admin-area/sandbox-execution/bonus/start/")
        self.assertEqual(denied.status_code, 404)
        self.assertIsNone(RoutingConfigurationRepository().load())

        self.client.force_login(self.staff)
        allowed = self.client.post("/admin-area/sandbox-execution/bonus/start/")
        self.assertRedirects(allowed, "/dashboard/")
        configuration = RoutingConfigurationRepository().load()
        assert configuration is not None
        self.assertTrue(configuration.bonus.execution)
        self.assertTrue(configuration.bonus.execution_sandbox)
        stopped = self.client.post("/admin-area/sandbox-execution/bonus/stop/")
        self.assertRedirects(stopped, "/dashboard/")
        configuration = RoutingConfigurationRepository().load()
        assert configuration is not None
        self.assertFalse(configuration.bonus.execution)
        self.assertFalse(configuration.bonus.execution_sandbox)
        self.assertEqual(ModeWorkQueueRow.objects.count(), 0)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
