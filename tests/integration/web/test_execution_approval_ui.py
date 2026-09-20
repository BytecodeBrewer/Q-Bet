from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.calculations import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.execution.models import ExecutionRecord, Lifecycle
from qbet.storage.ledger import (
    ExecutionStateRepository,
    ModeWorkQueueRepository,
)
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.queue import WorkState
from qbet.workflow.routing import EngineModes, RoutingConfiguration

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
CORRELATION_ID = UUID("22345678-1234-5678-1234-567812345678")


def _request(opportunity_id: str = "web-approval-opportunity") -> BonusEngineRequest:
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


class ExecutionApprovalWebTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("approval-owner", password="Strong-pass-123")
        self.other = User.objects.create_user("approval-other", password="Strong-pass-123")

    def _stage_execution(self) -> UUID:
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(bonus=EngineModes(execution=True)),
            queue_repository=ModeWorkQueueRepository(),
        )
        request = _request()
        now = datetime.now(UTC)
        (scheduled,) = coordinator.schedule(
            request,
            owner=self.user.get_username(),
            correlation_id=CORRELATION_ID,
            scheduled_for=now,
            expires_at=now + timedelta(minutes=5),
        )
        (waiting,) = coordinator.dispatch_due(
            now=now,
            owner=self.user.get_username(),
        )
        self.assertEqual(waiting.state, WorkState.RECHECK)
        return scheduled.work.id
    def test_owner_sees_only_business_level_pending_approval(self) -> None:
        execution_id = self._stage_execution()
        self.client.force_login(self.user)

        response = self.client.get("/execution/approvals/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Execution approvals")
        self.assertContains(response, "BonusEngine")
        self.assertContains(response, "web-approval-opportunity")
        self.assertContains(response, "Capital required")
        self.assertContains(response, "Approve")
        self.assertContains(response, "Reject")
        self.assertNotContains(response, str(CORRELATION_ID))
        self.assertNotContains(response, "RequestHandler")
        self.assertNotContains(response, "PortfolioLedger")

        self.client.force_login(self.other)
        hidden = self.client.get("/execution/approvals/")
        self.assertEqual(hidden.status_code, 200)
        self.assertContains(hidden, "No pending approvals")
        self.assertNotContains(hidden, "web-approval-opportunity")

        self.client.force_login(self.user)
        decision = self.client.post(
            f"/execution/approvals/{execution_id}/decision/",
            {"decision": "approve"},
            follow=True,
        )
        self.assertContains(decision, "Approval recorded")
        state = ExecutionStateRepository().load(execution_id)
        assert state is not None
        record, ledger = state
        queue = ModeWorkQueueRepository().load(execution_id)
        assert queue is not None
        self.assertIsInstance(record, ExecutionRecord)
        self.assertEqual(record.state, Lifecycle.APPROVED)
        self.assertFalse(ledger.commands)
        self.assertEqual(queue.state, WorkState.PENDING)

    def test_rejection_and_unauthorized_decision_are_safe(self) -> None:
        execution_id = self._stage_execution()
        self.client.force_login(self.other)

        denied = self.client.post(
            f"/execution/approvals/{execution_id}/decision/",
            {"decision": "approve"},
        )
        self.assertEqual(denied.status_code, 404)
        state = ExecutionStateRepository().load(execution_id)
        assert state is not None
        record, ledger = state
        self.assertEqual(record.state, Lifecycle.AWAITING_APPROVAL)
        self.assertFalse(ledger.commands)

        self.client.force_login(self.user)
        rejected = self.client.post(
            f"/execution/approvals/{execution_id}/decision/",
            {"decision": "reject"},
            follow=True,
        )
        self.assertContains(rejected, "Execution rejected")
        state = ExecutionStateRepository().load(execution_id)
        assert state is not None
        record, ledger = state
        queue = ModeWorkQueueRepository().load(execution_id)
        assert queue is not None
        self.assertEqual(record.state, Lifecycle.REJECTED)
        self.assertFalse(ledger.commands)
        self.assertEqual(queue.state, WorkState.CANCELLED)
