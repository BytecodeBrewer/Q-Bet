from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth.models import User
from django.test import TransactionTestCase

from qbet.execution.models import Lifecycle
from qbet.monitoring import MonitoringQuery
from qbet.request_handler import RevalidationOutcome
from qbet.storage.ledger import (
    ExecutionStateRepository,
    ModeWorkQueueRepository,
    RoutingConfigurationRepository,
)
from qbet.storage.models import ModeWorkQueueRow, PortfolioLedgerRow
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.web.models import SimulationAvailability
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.models import WorkflowMode
from qbet.workflow.queue import WorkState
from tests.support.workflow import bonus_request, sandbox_mode_handlers

NOW = datetime(2026, 9, 11, 3, 30, tzinfo=UTC)
CORRELATION_ID = UUID("32345678-1234-5678-1234-567812345678")
REJECTED_CORRELATION_ID = UUID("42345678-1234-5678-1234-567812345678")


class Phase2OperabilityGateTests(TransactionTestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            "phase2-owner",
            password="Strong-pass-123",
        )
        self.staff = User.objects.create_user(
            "phase2-admin",
            password="Strong-pass-123",
            is_staff=True,
        )
        SimulationAvailability.objects.create(pk=1, enabled=True)

    def _save_routing(self, *, bonus: str, sports_capital: str = "inactive") -> None:
        self.client.force_login(self.staff)
        response = self.client.post(
            "/admin-area/gui-settings/",
            {"bonus": bonus, "sports_capital": sports_capital},
        )
        self.assertEqual(response.status_code, 302)

    def _approve(self, execution_id) -> None:
        self.client.force_login(self.user)
        with patch("qbet.workflow.approval.datetime") as approval_clock:
            approval_clock.now.return_value = NOW
            response = self.client.post(
                f"/execution/approvals/{execution_id}/decision/",
                {"decision": "approve"},
            )
        self.assertEqual(response.status_code, 302)

    def _coordinator(
        self,
        opportunity_id: str,
        outcome: RevalidationOutcome = RevalidationOutcome.VALID,
    ) -> ModeDispatchCoordinator:
        return ModeDispatchCoordinator(
            routing_configuration_loader=RoutingConfigurationRepository().load,
            queue_repository=ModeWorkQueueRepository(),
            mode_request_handlers=sandbox_mode_handlers(
                opportunity_id,
                observed_at=NOW,
                execution_outcome=outcome,
            ),
        )

    def test_gui_routing_modes_remain_deterministic_before_user_intent(self) -> None:
        cases = (
            ("inactive", ()),
            ("simulation", (WorkflowMode.SIMULATION,)),
            ("execution", (WorkflowMode.EXECUTION,)),
            ("both", (WorkflowMode.SIMULATION, WorkflowMode.EXECUTION)),
        )
        for index, (selection, expected_modes) in enumerate(cases):
            with self.subTest(selection=selection):
                ModeWorkQueueRow.objects.all().delete()
                self._save_routing(bonus=selection)
                configuration = RoutingConfigurationRepository().load()
                self.assertIsNotNone(configuration)
                self.assertEqual(ModeWorkQueueRow.objects.count(), 0)
                self.assertEqual(PortfolioLedgerRow.objects.count(), 0)

                scheduled = self._coordinator(f"route-{selection}").schedule(
                    bonus_request(f"route-{selection}", generated_at=NOW),
                    owner=self.user.get_username(),
                    correlation_id=UUID(int=index + 1),
                    scheduled_for=NOW,
                    expires_at=NOW + timedelta(minutes=5),
                )
                self.assertEqual(tuple(item.work.mode for item in scheduled), expected_modes)

    def test_connected_success_path_isolated_approved_replay_safe_and_reconstructable(self) -> None:
        opportunity_id = "phase2-success"
        self._save_routing(bonus="both")
        configuration = RoutingConfigurationRepository().load()
        assert configuration is not None
        self.assertTrue(configuration.bonus.simulation)
        self.assertTrue(configuration.bonus.execution)
        self.assertEqual(ModeWorkQueueRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)

        coordinator = self._coordinator(opportunity_id)
        scheduled = coordinator.schedule(
            bonus_request(opportunity_id, generated_at=NOW),
            owner=self.user.get_username(),
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        self.assertEqual(
            {item.work.mode for item in scheduled},
            {WorkflowMode.SIMULATION, WorkflowMode.EXECUTION},
        )
        execution_work = next(
            item for item in scheduled if item.work.mode is WorkflowMode.EXECUTION
        )

        processed = coordinator.dispatch_due(now=NOW, owner=self.user.get_username())
        by_mode = {item.work.mode: item for item in processed}
        self.assertEqual(by_mode[WorkflowMode.SIMULATION].state, WorkState.COMPLETED)
        self.assertEqual(by_mode[WorkflowMode.EXECUTION].state, WorkState.RECHECK)
        self.assertEqual(
            by_mode[WorkflowMode.EXECUTION].history[-1].reason,
            "execution_approval_required",
        )

        self.client.force_login(self.user)
        approvals = self.client.get("/execution/approvals/")
        self.assertEqual(approvals.status_code, 200)
        self.assertContains(approvals, opportunity_id)
        self.assertNotContains(approvals, str(CORRELATION_ID))
        self.assertNotContains(approvals, "RequestHandler")
        self.assertNotContains(approvals, "PortfolioLedger")

        self._approve(execution_work.work.id)

        recreated = self._coordinator(opportunity_id)
        (completed,) = recreated.dispatch_due(
            now=NOW + timedelta(seconds=1),
            owner=self.user.get_username(),
        )
        self.assertEqual(completed.state, WorkState.COMPLETED)
        persisted = ExecutionStateRepository().load(execution_work.work.id)
        assert persisted is not None
        record, execution_ledger = persisted
        self.assertEqual(record.state, Lifecycle.SETTLED)
        self.assertTrue(execution_ledger.commands)

        simulation_rows = ModeWorkQueueRow.objects.filter(
            correlation_id=CORRELATION_ID,
            mode=WorkflowMode.SIMULATION.value,
        )
        execution_rows = ModeWorkQueueRow.objects.filter(
            correlation_id=CORRELATION_ID,
            mode=WorkflowMode.EXECUTION.value,
        )
        self.assertEqual(simulation_rows.count(), 1)
        self.assertEqual(execution_rows.count(), 1)
        self.assertEqual(
            PortfolioLedgerRow.objects.filter(mode=WorkflowMode.SIMULATION.value).count(),
            1,
        )
        self.assertEqual(
            PortfolioLedgerRow.objects.filter(mode=WorkflowMode.EXECUTION.value).count(),
            1,
        )

        command_count = len(execution_ledger.commands)
        replay = self.client.post(
            f"/execution/approvals/{execution_work.work.id}/decision/",
            {"decision": "approve"},
        )
        self.assertEqual(replay.status_code, 302)
        self.assertEqual(
            recreated.dispatch_due(
                now=NOW + timedelta(seconds=2),
                owner=self.user.get_username(),
            ),
            (),
        )
        replayed = ExecutionStateRepository().load(execution_work.work.id)
        assert replayed is not None
        replayed_record, replayed_ledger = replayed
        self.assertEqual(replayed_record, record)
        self.assertEqual(len(replayed_ledger.commands), command_count)

        records = PostgresMonitoringRepository().list_records(
            MonitoringQuery(
                start=NOW - timedelta(minutes=1),
                end=NOW + timedelta(minutes=1),
                correlation_id=CORRELATION_ID,
            )
        )
        self.assertTrue(records)
        self.assertEqual(
            {str(item.mode) for item in records if item.mode is not None},
            {WorkflowMode.SIMULATION.value, WorkflowMode.EXECUTION.value},
        )

        self.client.force_login(self.staff)
        export = self.client.get(
            "/monitoring/export/json/",
            {
                "start": (NOW - timedelta(minutes=1)).isoformat(),
                "end": (NOW + timedelta(minutes=1)).isoformat(),
                "correlation": str(CORRELATION_ID),
                "view": "extended",
            },
        )
        self.assertEqual(export.status_code, 200)
        self.assertIn(str(CORRELATION_ID), export.content.decode())

        self.client.force_login(self.user)
        dashboard = self.client.get("/dashboard/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertNotContains(dashboard, str(CORRELATION_ID))
        self.assertNotContains(dashboard, "RequestHandler")
        self.assertEqual(self.client.get("/monitoring/").status_code, 302)

    def test_rejected_final_revalidation_is_safe_and_reconstructable(self) -> None:
        opportunity_id = "phase2-rejected"
        self._save_routing(bonus="execution")
        coordinator = self._coordinator(opportunity_id)
        (scheduled,) = coordinator.schedule(
            bonus_request(opportunity_id, generated_at=NOW),
            owner=self.user.get_username(),
            correlation_id=REJECTED_CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        (waiting,) = coordinator.dispatch_due(now=NOW, owner=self.user.get_username())
        self.assertEqual(waiting.state, WorkState.RECHECK)

        self._approve(scheduled.work.id)

        rejected = self._coordinator(
            opportunity_id,
            RevalidationOutcome.REJECTED,
        ).dispatch_due(
            now=NOW + timedelta(seconds=1),
            owner=self.user.get_username(),
        )
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].state, WorkState.CANCELLED)
        persisted = ExecutionStateRepository().load(scheduled.work.id)
        assert persisted is not None
        record, ledger = persisted
        self.assertEqual(record.state, Lifecycle.CANCELLED)
        self.assertFalse(ledger.commands)

        records = PostgresMonitoringRepository().list_records(
            MonitoringQuery(
                start=NOW - timedelta(minutes=1),
                end=NOW + timedelta(minutes=1),
                correlation_id=REJECTED_CORRELATION_ID,
            )
        )
        self.assertTrue(records)
        self.assertTrue(
            any(
                str(item.status) in {"rejected", "cancelled", "reject"}
                or str(item.reason_code or "")
                in {"fixture_revalidation", "revalidation_rejected"}
                for item in records
            )
        )

        self.client.force_login(self.staff)
        export = self.client.get(
            "/monitoring/export/json/",
            {
                "start": (NOW - timedelta(minutes=1)).isoformat(),
                "end": (NOW + timedelta(minutes=1)).isoformat(),
                "correlation": str(REJECTED_CORRELATION_ID),
                "view": "extended",
            },
        )
        self.assertEqual(export.status_code, 200)
        body = export.content.decode()
        self.assertIn(str(REJECTED_CORRELATION_ID), body)
        self.assertNotIn("Traceback", body)
        self.assertNotIn("password", body.lower())
