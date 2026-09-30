from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID

from django.test import TransactionTestCase

from qbet.execution.models import ExecutionRecord, Lifecycle
from qbet.request_handler import RevalidationOutcome
from qbet.storage.ledger import ExecutionStateRepository, ModeWorkQueueRepository
from qbet.storage.models import ExecutionRecordRow
from qbet.workflow.approval import ExecutionApprovalService
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.queue import WorkState
from qbet.workflow.routing import EngineModes, RoutingConfiguration
from tests.support.workflow import bonus_request, sandbox_mode_handlers

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def _request(opportunity_id: str = "approval-opportunity"):
    return bonus_request(opportunity_id, generated_at=NOW)


def _coordinator(
    opportunity_id: str,
    outcome: RevalidationOutcome = RevalidationOutcome.VALID,
) -> ModeDispatchCoordinator:
    return ModeDispatchCoordinator(
        RoutingConfiguration(bonus=EngineModes(execution=True)),
        queue_repository=ModeWorkQueueRepository(),
        mode_request_handlers=sandbox_mode_handlers(
            opportunity_id,
            observed_at=NOW,
            execution_outcome=outcome,
        ),
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
        pending = recreated.pending_for("owner", now=NOW)
        self.assertEqual(tuple(item.execution_id for item in pending), (scheduled.work.id,))
        self.assertEqual(recreated.pending_for("other", now=NOW), ())
        self.assertEqual(recreated.active_count_for("owner", now=NOW), 1)
        self.assertEqual(
            recreated.active_count_for("owner", now=NOW + timedelta(minutes=5)),
            0,
        )

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

    def test_expired_approval_projection_is_read_only(self) -> None:
        scheduled, _ = self._stage()
        approvals = ExecutionApprovalService()
        before = ExecutionStateRepository().load(scheduled.work.id)
        before_queue = ModeWorkQueueRepository().load(scheduled.work.id)
        assert before is not None and before_queue is not None
        before_record, before_ledger = before

        with patch.object(approvals._monitoring_writer, "append") as monitoring:
            first = approvals.pending_for(
                "owner",
                now=NOW + timedelta(minutes=5),
            )
            second = approvals.pending_for(
                "owner",
                now=NOW + timedelta(minutes=6),
            )

        persisted = ExecutionStateRepository().load(scheduled.work.id)
        queue = ModeWorkQueueRepository().load(scheduled.work.id)
        assert persisted is not None and queue is not None
        record, ledger = persisted
        self.assertEqual(first, ())
        self.assertEqual(second, ())
        self.assertEqual(record, before_record)
        self.assertEqual(record.state, Lifecycle.AWAITING_APPROVAL)
        self.assertEqual(queue, before_queue)
        self.assertEqual(queue.state, WorkState.RECHECK)
        self.assertEqual(ledger.commands, before_ledger.commands)
        monitoring.assert_not_called()

    def test_worker_reconciles_expired_approval_without_dispatch(self) -> None:
        scheduled, _ = self._stage()
        approvals = ExecutionApprovalService()

        with patch(
            "qbet.execution.service.BonusSandboxAdapter.dispatch",
            side_effect=AssertionError("expired approval maintenance must never dispatch"),
        ):
            reconciled = approvals.reconcile_expired(
                now=NOW + timedelta(minutes=5),
                limit=10,
            )

        self.assertEqual(reconciled, (scheduled.work.id,))
        persisted = ExecutionStateRepository().load(scheduled.work.id)
        queue = ModeWorkQueueRepository().load(scheduled.work.id)
        assert persisted is not None and queue is not None
        record, ledger = persisted
        self.assertEqual(record.state, Lifecycle.CANCELLED)
        self.assertEqual(record.error, "approval_expired")
        self.assertEqual(queue.state, WorkState.CANCELLED)
        self.assertEqual(queue.history[-1].reason, "approval_expired")
        self.assertFalse(ledger.commands)

        repeated = approvals.reconcile_expired(
            now=NOW + timedelta(minutes=6),
            limit=10,
        )
        self.assertEqual(repeated, ())

    def test_approval_at_deadline_fails_closed_as_expired(self) -> None:
        scheduled, _ = self._stage()

        with patch(
            "qbet.execution.service.BonusSandboxAdapter.dispatch",
            side_effect=AssertionError("expired approval must never dispatch"),
        ):
            record = ExecutionApprovalService().decide(
                scheduled.work.id,
                actor="owner",
                approve=True,
                now=NOW + timedelta(minutes=5),
            )

        persisted = ExecutionStateRepository().load(scheduled.work.id)
        queue = ModeWorkQueueRepository().load(scheduled.work.id)
        assert persisted is not None and queue is not None
        _, ledger = persisted
        self.assertEqual(record.state, Lifecycle.CANCELLED)
        self.assertEqual(record.error, "approval_expired")
        self.assertEqual(queue.state, WorkState.CANCELLED)
        self.assertEqual(queue.history[-1].reason, "approval_expired")
        self.assertFalse(ledger.commands)

        replay = ExecutionApprovalService().decide(
            scheduled.work.id,
            actor="owner",
            approve=True,
            now=NOW + timedelta(minutes=5, seconds=1),
        )
        self.assertEqual(replay.state, Lifecycle.CANCELLED)
        self.assertEqual(replay.error, "approval_expired")

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
