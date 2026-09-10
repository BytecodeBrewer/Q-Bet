from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from django.test import TransactionTestCase

from qbet.calculations import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.execution.models import ExecutionRecord, Lifecycle
from qbet.request_handler import (
    ExecutionSandboxRequestHandler,
    ModeRequestHandlers,
    RevalidationOutcome,
    SandboxRevalidationFixture,
)
from qbet.storage.ledger import ExecutionStateRepository, ModeWorkQueueRepository
from qbet.storage.models import ExecutionRecordRow
from qbet.workflow.approval import ExecutionApprovalService
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.queue import WorkState
from qbet.workflow.routing import EngineModes, RoutingConfiguration

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def _request(opportunity_id: str = "approval-opportunity") -> BonusEngineRequest:
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


def _handlers(
    opportunity_id: str,
    outcome: RevalidationOutcome = RevalidationOutcome.VALID,
) -> ModeRequestHandlers:
    return ModeRequestHandlers(
        execution=ExecutionSandboxRequestHandler(
            revalidation_fixtures=(
                SandboxRevalidationFixture(
                    opportunity_id=opportunity_id,
                    outcome=outcome,
                    validated_at=NOW,
                    reason_code=(
                        None if outcome is RevalidationOutcome.VALID else "fixture_revalidation"
                    ),
                ),
            )
        )
    )


def _coordinator(
    opportunity_id: str,
    outcome: RevalidationOutcome = RevalidationOutcome.VALID,
) -> ModeDispatchCoordinator:
    return ModeDispatchCoordinator(
        RoutingConfiguration(bonus=EngineModes(execution=True)),
        queue_repository=ModeWorkQueueRepository(),
        mode_request_handlers=_handlers(opportunity_id, outcome),
    )


class ExecutionApprovalBoundaryTests(TransactionTestCase):
    def _stage(self, *, owner: str = "owner", opportunity_id: str = "approval-opportunity"):
        coordinator = _coordinator(opportunity_id)
        (scheduled,) = coordinator.schedule(
            _request(opportunity_id),
            owner=owner,
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        with patch(
            "qbet.execution.service.BonusSandboxAdapter.dispatch",
            side_effect=AssertionError("adapter must not run before approval"),
        ):
            (waiting,) = coordinator.dispatch_due(now=NOW, owner=owner)
        return scheduled, waiting

    def test_due_execution_waits_durably_for_explicit_approval(self) -> None:
        scheduled, waiting = self._stage()

        self.assertEqual(waiting.state, WorkState.RECHECK)
        self.assertEqual(waiting.history[-1].reason, "execution_approval_required")
        self.assertEqual(ExecutionRecordRow.objects.count(), 1)
        row = ExecutionRecordRow.objects.get(pk=scheduled.work.id)
        self.assertEqual(row.state, Lifecycle.AWAITING_APPROVAL.value)
        record, ledger = ExecutionStateRepository().load(scheduled.work.id) or (None, None)
        self.assertIsInstance(record, ExecutionRecord)
        assert record is not None and ledger is not None
        self.assertEqual(record.state, Lifecycle.AWAITING_APPROVAL)
        self.assertEqual(record.proposal.work.owner, "owner")
        self.assertFalse(ledger.commands)

        recreated = ExecutionApprovalService()
        pending = recreated.pending_for("owner")
        self.assertEqual(tuple(item.execution_id for item in pending), (scheduled.work.id,))
        self.assertEqual(recreated.pending_for("other"), ())

    def test_approval_survives_recreation_revalidates_and_dispatches_once(self) -> None:
        scheduled, _ = self._stage()
        approvals = ExecutionApprovalService()

        approved = approvals.decide(
            scheduled.work.id,
            actor="owner",
            approve=True,
            now=NOW + timedelta(seconds=10),
        )
        queued = ModeWorkQueueRepository().load(scheduled.work.id)
        assert queued is not None
        self.assertEqual(approved.state, Lifecycle.APPROVED)
        self.assertEqual(queued.state, WorkState.PENDING)

        recreated = _coordinator(scheduled.request.opportunity_id)
        (completed,) = recreated.dispatch_due(
            now=NOW + timedelta(seconds=11),
            owner="owner",
        )
        self.assertEqual(completed.state, WorkState.COMPLETED)
        persisted = ExecutionStateRepository().load(scheduled.work.id)
        assert persisted is not None
        record, ledger = persisted
        self.assertEqual(record.state, Lifecycle.SETTLED)
        self.assertIsNotNone(record.approval)
        self.assertTrue(ledger.commands)

        command_count = len(ledger.commands)
        repeated = ExecutionApprovalService().decide(
            scheduled.work.id,
            actor="owner",
            approve=True,
            now=NOW + timedelta(seconds=12),
        )
        replayed = ExecutionStateRepository().load(scheduled.work.id)
        assert replayed is not None
        replayed_record, replayed_ledger = replayed
        self.assertEqual(repeated.state, Lifecycle.SETTLED)
        self.assertEqual(replayed_record, record)
        self.assertEqual(len(replayed_ledger.commands), command_count)
        queue_after_replay = ModeWorkQueueRepository().load(scheduled.work.id)
        assert queue_after_replay is not None
        self.assertEqual(queue_after_replay.state, WorkState.COMPLETED)

    def test_rejection_is_terminal_without_reservation_or_dispatch(self) -> None:
        scheduled, _ = self._stage()

        with patch(
            "qbet.execution.service.BonusSandboxAdapter.dispatch",
            side_effect=AssertionError("rejected execution must never dispatch"),
        ):
            rejected = ExecutionApprovalService().decide(
                scheduled.work.id,
                actor="owner",
                approve=False,
                now=NOW + timedelta(seconds=10),
            )

        queued = ModeWorkQueueRepository().load(scheduled.work.id)
        persisted = ExecutionStateRepository().load(scheduled.work.id)
        assert queued is not None and persisted is not None
        _, ledger = persisted
        self.assertEqual(rejected.state, Lifecycle.REJECTED)
        self.assertEqual(queued.state, WorkState.CANCELLED)
        self.assertFalse(ledger.commands)

    def test_final_revalidation_after_approval_cancels_stale_plan_before_dispatch(self) -> None:
        scheduled, _ = self._stage()
        ExecutionApprovalService().decide(
            scheduled.work.id,
            actor="owner",
            approve=True,
            now=NOW + timedelta(seconds=10),
        )

        with patch(
            "qbet.execution.service.BonusSandboxAdapter.dispatch",
            side_effect=AssertionError("failed final revalidation must not dispatch"),
        ):
            (cancelled,) = _coordinator(
                scheduled.request.opportunity_id,
                RevalidationOutcome.REJECTED,
            ).dispatch_due(
                now=NOW + timedelta(seconds=11),
                owner="owner",
            )

        persisted = ExecutionStateRepository().load(scheduled.work.id)
        assert persisted is not None
        record, ledger = persisted
        self.assertEqual(cancelled.state, WorkState.CANCELLED)
        self.assertEqual(record.state, Lifecycle.CANCELLED)
        self.assertFalse(ledger.commands)

    def test_unauthorized_actor_cannot_approve_another_owners_work(self) -> None:
        scheduled, _ = self._stage(owner="alice")

        with self.assertRaisesMessage(PermissionError, "proposal_owner_required"):
            ExecutionApprovalService().decide(
                scheduled.work.id,
                actor="bob",
                approve=True,
                now=NOW + timedelta(seconds=10),
            )

        persisted = ExecutionStateRepository().load(scheduled.work.id)
        assert persisted is not None
        record, ledger = persisted
        self.assertEqual(record.state, Lifecycle.AWAITING_APPROVAL)
        self.assertFalse(ledger.commands)
        queued = ModeWorkQueueRepository().load(scheduled.work.id)
        assert queued is not None
        self.assertEqual(queued.state, WorkState.RECHECK)
