from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.calculations.qualifying_bet import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.storage.models import ExecutionRecordRow, ModeWorkQueueRow, PortfolioLedgerRow
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.models import WorkflowMode

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def _bonus_request(opportunity_id: str) -> BonusEngineRequest:
    return BonusEngineRequest(
        opportunity_id=opportunity_id,
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100"),
        ),
        currency="EUR",
        execution_offer_ids=("book", "exchange"),
        generated_at=NOW,
    )


class StoredRoutingDispatchTests(TestCase):
    def setUp(self) -> None:
        self.staff = User.objects.create_user(
            "routing-admin",
            password="Strong-pass-123",
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_saved_admin_configuration_routes_into_isolated_mode_queues_and_reloads_fresh(self) -> None:
        saved = self.client.post(
            "/admin-area/gui-settings/",
            {"bonus": "both", "sports_capital": "inactive"},
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(ModeWorkQueueRow.objects.count(), 0)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)

        coordinator = ModeDispatchCoordinator()
        first = coordinator.schedule(
            _bonus_request("bonus-routing-1"),
            owner="owner",
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        self.assertEqual(
            tuple(item.work.mode for item in first),
            (WorkflowMode.SIMULATION, WorkflowMode.EXECUTION),
        )
        self.assertNotEqual(first[0].work.id, first[1].work.id)
        self.assertNotEqual(first[0].work.capital_context, first[1].work.capital_context)
        self.assertEqual(ModeWorkQueueRow.objects.count(), 2)
        self.assertEqual(
            set(ModeWorkQueueRow.objects.values_list("mode", flat=True)),
            {"simulation", "execution"},
        )
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)

        repeat = coordinator.schedule(
            _bonus_request("bonus-routing-1"),
            owner="owner",
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        self.assertEqual(repeat, first)
        self.assertEqual(ModeWorkQueueRow.objects.count(), 2)

        changed = self.client.post(
            "/admin-area/gui-settings/",
            {"bonus": "execution", "sports_capital": "inactive"},
        )
        self.assertEqual(changed.status_code, 302)

        second = coordinator.schedule(
            _bonus_request("bonus-routing-2"),
            owner="owner",
            correlation_id=UUID("87654321-4321-8765-4321-876543218765"),
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        self.assertEqual(tuple(item.work.mode for item in second), (WorkflowMode.EXECUTION,))
        self.assertEqual(ModeWorkQueueRow.objects.count(), 3)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)
