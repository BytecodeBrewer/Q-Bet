from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.domain.ledger import LedgerCommand, LedgerOperation, PortfolioBalance
from qbet.execution.models import Lifecycle
from qbet.ledger import PortfolioLedger
from qbet.simulation import SimulationEngine
from qbet.storage.ledger import (
    ExecutionStateRepository,
    ModeWorkQueueRepository,
    PortfolioLedgerRepository,
    RoutingConfigurationRepository,
)
from qbet.storage.models import ExecutionRecordRow, PortfolioLedgerRow
from qbet.storage.simulation_ledger import SimulationPortfolioLedgerRepository
from qbet.web.models import SimulationAvailability, SimulationRunState
from qbet.web.simulation_control import SimulationControlService
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.queue import WorkState
from qbet.workflow.routing import EngineModes, RoutingConfiguration
from tests.support.workflow import bonus_request, sandbox_mode_handlers

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
ALICE_CORRELATION = UUID("32345678-1234-5678-1234-567812345678")
BOB_CORRELATION = UUID("42345678-1234-5678-1234-567812345678")


def _request(opportunity_id: str):
    return bonus_request(opportunity_id, generated_at=NOW)


def _handlers(opportunity_id: str):
    return sandbox_mode_handlers(opportunity_id, observed_at=NOW)


class DurableSimulationAndDispatchTests(TestCase):

    def test_gui_started_simulation_persists_and_recovers_simulation_ledger(self) -> None:
        staff = User.objects.create_user(
            "simulation-recovery-staff",
            password="Strong-pass-123",
            is_staff=True,
        )
        SimulationAvailability.objects.create(pk=1, enabled=True)
        RoutingConfigurationRepository().save(
            RoutingConfiguration(sports_capital=EngineModes(simulation=True))
        )
        self.client.force_login(staff)

        response = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.SPORTS_CAPITAL.value,
                "starting_capital": "125.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        run = SimulationRunState.objects.get()
        self.assertEqual(run.status, SimulationRunState.Status.RUNNING)
        executed = self.client.post(f"/simulation/{run.run_id}/run/")
        self.assertEqual(executed.status_code, 200)
        run.refresh_from_db()
        persisted = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertTrue(persisted.commands)
        self.assertEqual(run.current_capital, persisted.balance.available)
        self.assertEqual(PortfolioLedgerRow.objects.filter(mode="execution").count(), 0)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)

        recreated = SimulationControlService().snapshot()
        recovered_run = next(item for item in recreated.runs if item.run_id == run.run_id)
        recovered_ledger = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        self.assertEqual(recovered_run.current_capital, run.current_capital)
        self.assertEqual(recovered_ledger, persisted)

    def test_gui_simulation_preserves_existing_shared_ledger_history(self) -> None:
        repository = SimulationPortfolioLedgerRepository()
        initial = PortfolioLedger(
            balance=PortfolioBalance(
                mode="simulation",
                currency="EUR",
                available=Decimal("200"),
            )
        )
        repository.load_or_create(initial)
        seeded, decision = initial.apply(
            LedgerCommand(
                id="seed-cost",
                dispatch_id="seed",
                correlation_id="seed-correlation",
                currency="EUR",
                operation=LedgerOperation.COST,
                amount=Decimal("5"),
            )
        )
        self.assertTrue(decision.accepted)
        repository.merge(seeded)

        SimulationAvailability.objects.create(pk=1, enabled=True)
        RoutingConfigurationRepository().save(
            RoutingConfiguration(sports_capital=EngineModes(simulation=True))
        )
        staff = User.objects.create_user(
            "simulation-review-staff",
            password="Strong-pass-123",
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.SPORTS_CAPITAL.value,
                "starting_capital": "125.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        run = SimulationRunState.objects.get()
        executed = self.client.post(f"/simulation/{run.run_id}/run/")
        self.assertEqual(executed.status_code, 200)
        persisted = repository.load_or_create(initial)
        self.assertIn("seed-cost", persisted.commands)
        self.assertGreater(len(persisted.commands), 1)
        self.assertNotEqual(persisted.balance.available, Decimal("125.00"))

    def test_stale_simulation_snapshots_merge_without_losing_other_commands(self) -> None:
        repository = SimulationPortfolioLedgerRepository()
        baseline = repository.load_or_create(
            PortfolioLedger(
                balance=PortfolioBalance(
                    mode="simulation",
                    currency="EUR",
                    available=Decimal("100"),
                )
            )
        )
        first, first_decision = baseline.apply(
            LedgerCommand(
                id="cost-a",
                dispatch_id="a",
                correlation_id="a",
                currency="EUR",
                operation=LedgerOperation.COST,
                amount=Decimal("10"),
            )
        )
        second, second_decision = baseline.apply(
            LedgerCommand(
                id="cost-b",
                dispatch_id="b",
                correlation_id="b",
                currency="EUR",
                operation=LedgerOperation.COST,
                amount=Decimal("20"),
            )
        )
        self.assertTrue(first_decision.accepted)
        self.assertTrue(second_decision.accepted)

        repository.merge(first)
        merged = repository.merge(second)

        self.assertEqual(set(merged.commands), {"cost-a", "cost-b"})
        self.assertEqual(merged.balance.available, Decimal("70"))

    def test_global_due_claim_dispatches_each_item_under_its_persisted_owner(self) -> None:
        queue_repository = ModeWorkQueueRepository()
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(bonus=EngineModes(execution=True)),
            queue_repository=queue_repository,
        )
        (alice_item,) = coordinator.schedule(
            _request("alice-opportunity"),
            owner="alice",
            correlation_id=ALICE_CORRELATION,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        (bob_item,) = coordinator.schedule(
            _request("bob-opportunity"),
            owner="bob",
            correlation_id=BOB_CORRELATION,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        outcomes = coordinator.dispatch_due(now=NOW, owner="alice")
        by_owner = {item.work.owner: item for item in outcomes}

        self.assertEqual(set(by_owner), {"alice", "bob"})
        self.assertEqual(by_owner["alice"].state, WorkState.RECHECK)
        self.assertEqual(by_owner["bob"].state, WorkState.RECHECK)
        self.assertEqual(
            by_owner["bob"].history[-1].reason,
            "execution_approval_required",
        )

        alice_state = ExecutionStateRepository().load(alice_item.work.id)
        bob_state = ExecutionStateRepository().load(bob_item.work.id)
        assert alice_state is not None and bob_state is not None
        self.assertEqual(alice_state[0].state, Lifecycle.AWAITING_APPROVAL)
        self.assertEqual(bob_state[0].state, Lifecycle.AWAITING_APPROVAL)
        self.assertEqual(alice_state[0].proposal.work.owner, "alice")
        self.assertEqual(bob_state[0].proposal.work.owner, "bob")

    def test_routed_simulation_runs_directly_with_durable_merge(self) -> None:
        opportunity_id = "direct-routed-simulation"
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(bonus=EngineModes(simulation=True)),
            queue_repository=ModeWorkQueueRepository(),
            mode_request_handlers=_handlers(opportunity_id),
        )
        (item,) = coordinator.schedule(
            _request(opportunity_id),
            owner="owner",
            correlation_id=ALICE_CORRELATION,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        coordinator._run_simulation(item, now=NOW)
