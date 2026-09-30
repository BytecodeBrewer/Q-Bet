from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.contrib.auth.models import User
from django.test import TransactionTestCase

from qbet.calculations import QualifyingBetInput
from qbet.data import DataSourceMetadata, DataTarget, SourceTransport
from qbet.domain.models import OfferSide
from qbet.domain.verification import ProviderState
from qbet.engines import BonusEngineRequest, BonusOfferDependency
from qbet.execution.models import Lifecycle
from qbet.request_handler import ExpectedMarketOffer, TargetedMarketRevalidationContext
from qbet.simulation import SimulationEngine, SimulationRunConfig
from qbet.simulation.workflow import WorkflowSimulationRequest, WorkflowSimulationRunner
from qbet.storage.bonus_dependencies import (
    BonusDependencyState,
    PostgresBonusOfferDependencyValidator,
)
from qbet.storage.ledger import ExecutionStateRepository, ModeWorkQueueRepository
from qbet.storage.models import SportsbookProviderRow
from qbet.web.models import BonusOffer
from qbet.workflow.approval import ExecutionApprovalService
from qbet.workflow.bonus_dependencies import BonusOfferWorkInvalidator
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.models import WorkflowDecision, WorkflowStage
from qbet.workflow.queue import WorkState
from qbet.workflow.routing import EngineModes, RoutingConfiguration
from tests.support.workflow import sandbox_mode_handlers


NOW = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


class BonusOfferDependencyIntegrationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            "bonus-dependency-owner",
            password="Strong-pass-123",
        )
        self.provider = SportsbookProviderRow.objects.create(
            provider_id="dependency-book",
            legal_name="Dependency Book GmbH",
            display_name="Dependency Book",
            jurisdiction="DE",
            sports_betting=True,
            online=True,
            source_url="https://www.gluecksspiel-behoerde.de/",
            whitelist_snapshot_date=date(2026, 9, 7),
            status="active",
        )
        self.offer = BonusOffer.objects.create(
            user=self.user,
            provider=self.provider,
            name="Versioned reward",
            promotion_shape=BonusOffer.PromotionShape.BET_AND_GET,
            promotion_type=BonusOffer.PromotionType.QUALIFYING_BET,
            promotion_value=Decimal("10.00"),
            currency="EUR",
            required_stake=Decimal("10.00"),
            valid_until=NOW + timedelta(days=1),
        )

    def _request(self) -> BonusEngineRequest:
        dependency = BonusOfferDependency(
            offer_id=self.offer.pk,
            offer_version=self.offer.version,
            owner_id=self.user.pk,
            sport="tennis_atp",
            event_id="event-123",
            market="h2h",
        )
        return BonusEngineRequest(
            opportunity_id=(
                f"event-123:h2h:bonus-offer-{self.offer.pk}:v{self.offer.version}"
            ),
            inputs=QualifyingBetInput(
                back_odds=Decimal("2.5"),
                lay_odds=Decimal("2.6"),
                back_stake=Decimal("10"),
                exchange_commission=Decimal("0.02"),
                stake_precision=Decimal("0.01"),
                max_lay_liability=Decimal("100"),
            ),
            currency="EUR",
            execution_offer_ids=("promotion-offer", "hedge-offer"),
            bonus_offer_dependency=dependency,
            generated_at=NOW,
        )

    @staticmethod
    def _market_context() -> TargetedMarketRevalidationContext:
        return TargetedMarketRevalidationContext(
            source=DataSourceMetadata(
                provider_id="market-provider",
                source_id="market-source",
                transport=SourceTransport.API,
            ),
            target=DataTarget.BONUS,
            sport="tennis_atp",
            event_id="event-123",
            market="h2h",
            expected_offers=(
                ExpectedMarketOffer(
                    provider="dependency-book",
                    selection="home",
                    side=OfferSide.BACK,
                    odds=Decimal("2.5"),
                ),
            ),
            expires_at=NOW + timedelta(minutes=5),
        )

    def test_current_dependency_survives_domain_risk_and_execution_correlation(self) -> None:
        request = self._request()
        simulation = WorkflowSimulationRunner().run(
            WorkflowSimulationRequest(
                config=SimulationRunConfig(
                    engine=SimulationEngine.BONUS,
                    starting_capital=Decimal("100"),
                ),
                opportunities=(request,),
                provider_state=ProviderState(
                    provider_id=self.provider.provider_id,
                    active_bets_count=0,
                ),
                correlation_id=CORRELATION_ID,
            )
        )

        self.assertEqual(len(simulation.workflow_results), 1)
        risk = next(
            transition
            for transition in simulation.workflow_results[0].transitions
            if transition.stage is WorkflowStage.DOMAIN_RISK
        )
        self.assertEqual(risk.decision, WorkflowDecision.ALLOW)

        queue = ModeWorkQueueRepository()
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(
                bonus=EngineModes(execution=True, execution_sandbox=True)
            ),
            queue_repository=queue,
            mode_request_handlers=sandbox_mode_handlers(
                request.opportunity_id,
                observed_at=NOW,
            ),
        )
        (scheduled,) = coordinator.schedule(
            request,
            owner=self.user.username,
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
            market_revalidation=self._market_context(),
        )
        (processed,) = coordinator.dispatch_due(
            now=NOW + timedelta(seconds=1),
            owner=self.user.username,
        )

        self.assertEqual(processed.state, WorkState.RECHECK)
        loaded = ExecutionStateRepository().load(scheduled.work.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        record, _ = loaded
        self.assertEqual(record.state, Lifecycle.AWAITING_APPROVAL)
        self.assertEqual(record.proposal.request.bonus_offer_dependency, request.bonus_offer_dependency)
        self.assertEqual(record.proposal.work.opportunity_id, request.opportunity_id)
        self.assertIsNotNone(record.proposal.result_provider_target)
        assert record.proposal.result_provider_target is not None
        self.assertEqual(record.proposal.result_provider_target.event_id, "event-123")

    def test_offer_edit_marks_old_work_recheck_and_stale_request_cannot_dispatch(self) -> None:
        request = self._request()
        queue = ModeWorkQueueRepository()
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(bonus=EngineModes(simulation=True)),
            queue_repository=queue,
            mode_request_handlers=sandbox_mode_handlers(
                request.opportunity_id,
                observed_at=NOW,
            ),
        )
        (scheduled,) = coordinator.schedule(
            request,
            owner=self.user.username,
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        self.offer.version += 1
        self.offer.save(update_fields=("version", "updated_at"))
        invalidated = BonusOfferWorkInvalidator(
            queue_repository=queue
        ).invalidate(
            self.offer.pk,
            current_version=self.offer.version,
            now=NOW + timedelta(seconds=1),
            removed=False,
        )

        self.assertEqual(len(invalidated), 1)
        queued = queue.load(scheduled.work.id)
        self.assertIsNotNone(queued)
        assert queued is not None
        self.assertEqual(queued.state, WorkState.RECHECK)
        self.assertEqual(queued.history[-1].reason, "bonus_offer_version_changed")
        self.assertEqual(
            PostgresBonusOfferDependencyValidator().check(request).state,
            BonusDependencyState.STALE,
        )

        queue.reschedule(
            scheduled.work.id,
            scheduled_for=NOW + timedelta(seconds=2),
            now=NOW + timedelta(seconds=1),
        )
        (processed,) = coordinator.dispatch_due(
            now=NOW + timedelta(seconds=3),
            owner=self.user.username,
        )

        self.assertEqual(processed.state, WorkState.CANCELLED)
        self.assertEqual(processed.history[-1].reason, "bonus_offer_dependency_stale")

    def test_offer_remove_cancels_approved_execution_before_dispatch(self) -> None:
        request = self._request()
        queue = ModeWorkQueueRepository()
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(
                bonus=EngineModes(execution=True, execution_sandbox=True)
            ),
            queue_repository=queue,
            mode_request_handlers=sandbox_mode_handlers(
                request.opportunity_id,
                observed_at=NOW,
            ),
        )
        (scheduled,) = coordinator.schedule(
            request,
            owner=self.user.username,
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
            market_revalidation=self._market_context(),
        )
        coordinator.dispatch_due(
            now=NOW + timedelta(seconds=1),
            owner=self.user.username,
        )
        approved = ExecutionApprovalService(
            queue_repository=queue
        ).decide(
            scheduled.work.id,
            actor=self.user.username,
            approve=True,
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(approved.state, Lifecycle.APPROVED)

        self.offer.version += 1
        self.offer.retired_at = NOW + timedelta(seconds=3)
        self.offer.save(update_fields=("version", "retired_at", "updated_at"))
        BonusOfferWorkInvalidator(
            queue_repository=queue
        ).invalidate(
            self.offer.pk,
            current_version=self.offer.version,
            now=NOW + timedelta(seconds=3),
            removed=True,
        )

        queued = queue.load(scheduled.work.id)
        loaded = ExecutionStateRepository().load(scheduled.work.id)
        self.assertIsNotNone(queued)
        self.assertIsNotNone(loaded)
        assert queued is not None and loaded is not None
        record, ledger = loaded
        self.assertEqual(queued.state, WorkState.CANCELLED)
        self.assertEqual(queued.history[-1].reason, "bonus_offer_removed")
        self.assertEqual(record.state, Lifecycle.CANCELLED)
        self.assertEqual(record.error, "bonus_offer_removed")
        self.assertFalse(ledger.commands)

        self.assertEqual(
            coordinator.dispatch_due(
                now=NOW + timedelta(seconds=4),
                owner=self.user.username,
            ),
            (),
        )
