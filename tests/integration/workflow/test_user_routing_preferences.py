from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.test import TestCase

from qbet.calculations.qualifying_bet import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.storage.ledger import (
    RoutingConfigurationRepository,
    UserRoutingPreferenceRepository,
)
from qbet.storage.models import ExecutionRecordRow, ModeWorkQueueRow, PortfolioLedgerRow
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.models import WorkflowMode
from qbet.workflow.routing import (
    EngineModes,
    RoutingConfiguration,
    UserEngineModes,
    UserRoutingPreferences,
)

NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)


def _request(opportunity_id: str) -> BonusEngineRequest:
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


class UserRoutingPreferencesTests(TestCase):
    def test_preferences_persist_per_user_and_global_disable_only_blocks_new_work(self) -> None:
        routing = RoutingConfigurationRepository()
        preferences = UserRoutingPreferenceRepository()
        routing.save(
            RoutingConfiguration(bonus=EngineModes(simulation=True, execution=True))
        )
        preferences.save(
            "alice",
            UserRoutingPreferences(
                bonus=UserEngineModes(simulation=True, execution=True)
            ),
        )

        coordinator = ModeDispatchCoordinator()
        first = coordinator.schedule(
            _request("first"),
            owner="alice",
            correlation_id=UUID("11111111-1111-1111-1111-111111111111"),
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        bob = coordinator.schedule(
            _request("bob"),
            owner="bob",
            correlation_id=UUID("22222222-2222-2222-2222-222222222222"),
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        self.assertEqual(
            tuple(item.work.mode for item in first),
            (WorkflowMode.SIMULATION, WorkflowMode.EXECUTION),
        )
        self.assertNotEqual(first[0].work.id, first[1].work.id)
        self.assertNotEqual(first[0].work.capital_context, first[1].work.capital_context)
        self.assertEqual(bob, ())
        self.assertTrue(UserRoutingPreferenceRepository().load("alice").bonus.execution)

        routing.save(RoutingConfiguration(bonus=EngineModes(simulation=True)))
        second = coordinator.schedule(
            _request("second"),
            owner="alice",
            correlation_id=UUID("33333333-3333-3333-3333-333333333333"),
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        self.assertTrue(preferences.load("alice").bonus.execution)
        self.assertEqual(tuple(item.work.mode for item in second), (WorkflowMode.SIMULATION,))
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)
        self.assertEqual(ModeWorkQueueRow.objects.count(), 3)
