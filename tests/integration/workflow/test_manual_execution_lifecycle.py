from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.test import TransactionTestCase

from qbet.data import (
    DataSourceMetadata,
    DataTarget,
    DeterministicResultCollector,
    DeterministicResultFixture,
    NormalizedMatchResult,
    ResultAvailability,
    ResultCollectionRequest,
    ResultProviderTarget,
    SourceTransport,
)
from qbet.domain.models import OfferSide
from qbet.engines import BonusEngineRequest
from qbet.execution.models import Lifecycle, ManualExecutionDecision
from qbet.notifications import (
    CaptureEmailTransport,
    ExecutionNotificationService,
    InMemoryNotificationRepository,
    NotificationRecipient,
)
from qbet.request_handler import ExpectedMarketOffer, TargetedMarketRevalidationContext
from qbet.settlement.post_event import PostEventSettlementService
from qbet.storage.ledger import ExecutionStateRepository, ModeWorkQueueRepository
from qbet.workflow.approval import ExecutionApprovalService
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.manual_execution import ManualExecutionService
from qbet.workflow.queue import WorkState
from qbet.workflow.routing import EngineModes, RoutingConfiguration
from tests.support.workflow import bonus_request, sandbox_mode_handlers

NOW = datetime(2026, 9, 30, 8, 0, tzinfo=UTC)
CORRELATION_ID = UUID("32345678-1234-5678-1234-567812345678")
SOURCE = DataSourceMetadata(
    provider_id="official-results",
    source_id="manual-flow-results",
    transport=SourceTransport.API,
)
TARGET = ResultProviderTarget(sport="soccer", event_id="event-manual-214")


class OwnerRecipientResolver:
    def resolve(self) -> tuple[NotificationRecipient, ...]:
        return (
            NotificationRecipient(
                user_id="owner",
                email="owner@example.com",
                display_name="Owner",
            ),
        )


def market_context(*, reversed_order: bool = False) -> TargetedMarketRevalidationContext:
    offers = (
        ExpectedMarketOffer(
            provider="Book One",
            selection="Home",
            side=OfferSide.BACK,
            odds=Decimal("2.50"),
        ),
        ExpectedMarketOffer(
            provider="Book Two",
            selection="Away",
            side=OfferSide.LAY,
            odds=Decimal("2.60"),
        ),
    )
    if reversed_order:
        offers = tuple(reversed(offers))
    return TargetedMarketRevalidationContext(
        source=SOURCE,
        target=DataTarget.BONUS,
        sport=TARGET.sport,
        event_id=TARGET.event_id,
        market="match_winner",
        expected_offers=offers,
        expires_at=NOW + timedelta(minutes=30),
    )


class ManualExecutionLifecycleTests(TransactionTestCase):
    def stage(
        self,
        *,
        request: BonusEngineRequest | None = None,
        context: TargetedMarketRevalidationContext | None = None,
    ):
        opportunity_id = "manual-phase3-opportunity"
        selected_request = request or bonus_request(opportunity_id, generated_at=NOW)
        selected_context = context or market_context()
        transport = CaptureEmailTransport()
        notifications = ExecutionNotificationService(
            repository=InMemoryNotificationRepository(),
            transport=transport,
        )
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(
                bonus=EngineModes(
                    execution=True,
                    execution_sandbox=False,
                )
            ),
            queue_repository=ModeWorkQueueRepository(),
            mode_request_handlers=sandbox_mode_handlers(
                opportunity_id,
                observed_at=NOW,
            ),
            notification_service=notifications,
            notification_recipient_resolver=OwnerRecipientResolver(),
        )
        (scheduled,) = coordinator.schedule(
            selected_request,
            owner="owner",
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=30),
            market_revalidation=selected_context,
        )
        return scheduled, coordinator, transport

    def open_action_window(self):
        scheduled, coordinator, transport = self.stage()

        (waiting_approval,) = coordinator.dispatch_due(now=NOW, owner="owner")
        self.assertEqual(waiting_approval.state, WorkState.RECHECK)
        self.assertEqual(waiting_approval.history[-1].reason, "execution_approval_required")
        before_approval = ExecutionStateRepository().load(scheduled.work.id)
        assert before_approval is not None
        record, ledger = before_approval
        self.assertEqual(record.state, Lifecycle.AWAITING_APPROVAL)
        self.assertEqual(ledger.positions[str(scheduled.work.id)].state, "reserved")
        self.assertEqual(len(transport.messages), 0)

        approved = ExecutionApprovalService().decide(
            scheduled.work.id,
            actor="owner",
            approve=True,
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(approved.state, Lifecycle.APPROVED)

        (awaiting_confirmation,) = coordinator.dispatch_due(
            now=NOW + timedelta(seconds=2),
            owner="owner",
        )
        self.assertEqual(awaiting_confirmation.state, WorkState.RECHECK)
        self.assertEqual(
            awaiting_confirmation.history[-1].reason,
            "manual_action_confirmation_required",
        )
        state = ExecutionStateRepository().load(scheduled.work.id)
        assert state is not None
        record, ledger = state
        self.assertEqual(record.state, Lifecycle.AWAITING_CONFIRMATION)
        self.assertEqual(ledger.positions[str(scheduled.work.id)].state, "reserved")
        self.assertEqual(len(transport.messages), 1)
        return scheduled, record, ledger, transport

    def test_manual_execution_waits_for_explicit_user_confirmation_after_notification(self) -> None:
        scheduled, record, ledger, transport = self.open_action_window()

        actions = ManualExecutionService().pending_for(
            "owner",
            now=NOW + timedelta(seconds=3),
        )

        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.execution_id, scheduled.work.id)
        self.assertEqual(action.correlation_id, CORRELATION_ID)
        self.assertEqual(action.event_id, TARGET.event_id)
        self.assertEqual(
            tuple((item.provider, item.selection, item.side, item.odds) for item in action.instructions),
            (
                ("Book One", "Home", OfferSide.BACK, Decimal("2.50")),
                ("Book Two", "Away", OfferSide.LAY, Decimal("2.60")),
            ),
        )
        self.assertEqual(record.result, None)
        self.assertEqual(ledger.positions[str(scheduled.work.id)].state, "reserved")
        self.assertEqual(len(transport.messages), 1)

    def test_reordered_revalidation_offers_keep_instruction_identity(self) -> None:
        scheduled, coordinator, transport = self.stage(
            context=market_context(reversed_order=True)
        )

        coordinator.dispatch_due(now=NOW, owner="owner")
        ExecutionApprovalService().decide(
            scheduled.work.id,
            actor="owner",
            approve=True,
            now=NOW + timedelta(seconds=1),
        )
        coordinator.dispatch_due(now=NOW + timedelta(seconds=2), owner="owner")

        actions = ManualExecutionService().pending_for(
            "owner",
            now=NOW + timedelta(seconds=3),
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(
            tuple(
                (item.offer_id, item.provider, item.selection, item.side, item.odds)
                for item in actions[0].instructions
            ),
            (
                ("book", "Book One", "Home", OfferSide.BACK, Decimal("2.50")),
                ("exchange", "Book Two", "Away", OfferSide.LAY, Decimal("2.60")),
            ),
        )
        self.assertEqual(len(transport.messages), 1)

    def test_done_is_user_attested_pending_then_existing_result_boundary_settles_once(self) -> None:
        scheduled, _, _, _ = self.open_action_window()
        manual = ManualExecutionService()

        confirmed = manual.confirm(
            scheduled.work.id,
            actor="owner",
            decision=ManualExecutionDecision.DONE,
            now=NOW + timedelta(seconds=3),
        )

        persisted = ExecutionStateRepository().load(scheduled.work.id)
        assert persisted is not None
        record, ledger = persisted
        self.assertEqual(confirmed, record)
        self.assertEqual(record.state, Lifecycle.ACKNOWLEDGED)
        self.assertIsNotNone(record.result)
        assert record.result is not None
        self.assertEqual(record.result.source, "user_attested")
        self.assertEqual(record.manual_confirmations[-1].decision, ManualExecutionDecision.DONE)
        self.assertEqual(ledger.positions[str(scheduled.work.id)].state, "pending")
        self.assertEqual(len(ledger.commands), 3)

        replay = manual.confirm(
            scheduled.work.id,
            actor="owner",
            decision=ManualExecutionDecision.DONE,
            now=NOW + timedelta(seconds=4),
        )
        replayed = ExecutionStateRepository().load(scheduled.work.id)
        assert replayed is not None
        replayed_record, replayed_ledger = replayed
        self.assertEqual(replay, record)
        self.assertEqual(replayed_record, record)
        self.assertEqual(replayed_ledger, ledger)

        request = ResultCollectionRequest(
            match_id=scheduled.work.opportunity_id,
            execution_id=str(scheduled.work.id),
            correlation_id=CORRELATION_ID,
            source=SOURCE,
            fresh_after=NOW + timedelta(seconds=3),
            provider_target=TARGET,
        )
        result = NormalizedMatchResult(
            match_id=request.match_id,
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            source=SOURCE,
            availability=ResultAvailability.AVAILABLE,
            observed_at=NOW + timedelta(minutes=10),
            provider_outcome="success",
            provider_target=TARGET,
            completed=True,
        )
        settled = PostEventSettlementService(
            DeterministicResultCollector(
                (
                    DeterministicResultFixture(
                        result=result,
                    ),
                )
            )
        ).collect_and_settle(request)

        self.assertTrue(settled.settled)
        self.assertEqual(settled.record.state, Lifecycle.SETTLED)
        self.assertEqual(
            settled.ledger.positions[str(scheduled.work.id)].state,
            "settled",
        )
        queue = ModeWorkQueueRepository().load(scheduled.work.id)
        assert queue is not None
        self.assertEqual(queue.state, WorkState.COMPLETED)

    def test_not_done_releases_reservation_without_faking_provider_result(self) -> None:
        scheduled, _, _, _ = self.open_action_window()

        cancelled = ManualExecutionService().confirm(
            scheduled.work.id,
            actor="owner",
            decision=ManualExecutionDecision.NOT_DONE,
            now=NOW + timedelta(seconds=3),
        )

        persisted = ExecutionStateRepository().load(scheduled.work.id)
        queue = ModeWorkQueueRepository().load(scheduled.work.id)
        assert persisted is not None and queue is not None
        record, ledger = persisted
        self.assertEqual(cancelled.state, Lifecycle.CANCELLED)
        self.assertEqual(record.error, "manual_action_not_performed")
        self.assertIsNone(record.result)
        self.assertEqual(ledger.positions[str(scheduled.work.id)].state, "released")
        self.assertEqual(ledger.balance.available, Decimal("1000"))
        self.assertEqual(queue.state, WorkState.CANCELLED)

    def test_problem_keeps_reservation_and_can_later_be_resolved_as_done(self) -> None:
        scheduled, _, _, _ = self.open_action_window()
        manual = ManualExecutionService()

        problem = manual.confirm(
            scheduled.work.id,
            actor="owner",
            decision=ManualExecutionDecision.PROBLEM,
            note="Provider showed different odds.",
            now=NOW + timedelta(seconds=3),
        )
        problem_state = ExecutionStateRepository().load(scheduled.work.id)
        assert problem_state is not None
        _, problem_ledger = problem_state
        self.assertEqual(problem.state, Lifecycle.ACTION_PROBLEM)
        self.assertEqual(problem_ledger.positions[str(scheduled.work.id)].state, "reserved")

        done = manual.confirm(
            scheduled.work.id,
            actor="owner",
            decision=ManualExecutionDecision.DONE,
            now=NOW + timedelta(seconds=4),
        )
        final_state = ExecutionStateRepository().load(scheduled.work.id)
        assert final_state is not None
        _, final_ledger = final_state
        self.assertEqual(done.state, Lifecycle.ACKNOWLEDGED)
        self.assertEqual(
            tuple(item.decision for item in done.manual_confirmations),
            (ManualExecutionDecision.PROBLEM, ManualExecutionDecision.DONE),
        )
        self.assertEqual(final_ledger.positions[str(scheduled.work.id)].state, "pending")

    def test_background_expiry_marks_silent_action_missed_and_releases_capital(self) -> None:
        scheduled, _, _, _ = self.open_action_window()

        expired = ManualExecutionService().expire_due(
            now=NOW + timedelta(minutes=31),
        )

        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].proposal.work.id, scheduled.work.id)
        persisted = ExecutionStateRepository().load(scheduled.work.id)
        queue = ModeWorkQueueRepository().load(scheduled.work.id)
        assert persisted is not None and queue is not None
        record, ledger = persisted
        self.assertEqual(record.state, Lifecycle.CANCELLED)
        self.assertEqual(record.error, "manual_action_window_expired")
        self.assertEqual(ledger.positions[str(scheduled.work.id)].state, "released")
        self.assertEqual(queue.state, WorkState.CANCELLED)

    def test_reservation_failure_terminalizes_execution_and_queue(self) -> None:
        request = bonus_request(
            "manual-phase3-opportunity",
            generated_at=NOW,
            back_stake=Decimal("900"),
            max_lay_liability=Decimal("5000"),
        )
        scheduled, coordinator, transport = self.stage(request=request)

        (failed,) = coordinator.dispatch_due(now=NOW, owner="owner")

        persisted = ExecutionStateRepository().load(scheduled.work.id)
        assert persisted is not None
        record, ledger = persisted
        self.assertEqual(failed.state, WorkState.FAILED)
        self.assertEqual(record.state, Lifecycle.FAILED)
        self.assertEqual(record.error, "insufficient_available_capital")
        self.assertEqual(ledger.balance.available, Decimal("1000"))
        self.assertNotIn(str(scheduled.work.id), ledger.positions)
        self.assertEqual(
            ExecutionApprovalService().pending_for(
                "owner",
                now=NOW + timedelta(seconds=1),
            ),
            (),
        )
        self.assertEqual(len(transport.messages), 0)

    def test_rejection_after_preapproval_reservation_releases_capital(self) -> None:
        scheduled, coordinator, transport = self.stage()
        coordinator.dispatch_due(now=NOW, owner="owner")

        rejected = ExecutionApprovalService().decide(
            scheduled.work.id,
            actor="owner",
            approve=False,
            now=NOW + timedelta(seconds=1),
        )

        persisted = ExecutionStateRepository().load(scheduled.work.id)
        assert persisted is not None
        record, ledger = persisted
        self.assertEqual(rejected.state, Lifecycle.REJECTED)
        self.assertEqual(record.state, Lifecycle.REJECTED)
        self.assertEqual(ledger.positions[str(scheduled.work.id)].state, "released")
        self.assertEqual(ledger.balance.available, Decimal("1000"))
        self.assertEqual(len(transport.messages), 0)
