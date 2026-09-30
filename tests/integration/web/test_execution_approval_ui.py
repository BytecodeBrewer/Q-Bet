from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.db import DatabaseError
from django.test import TestCase

from qbet.calculations import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.execution.models import ExecutionRecord, Lifecycle
from qbet.storage.ledger import (
    AuthoritativePersistenceError,
    ExecutionRecordRepository,
    ExecutionStateRepository,
    ModeWorkQueueRepository,
)
from qbet.storage.models import ExecutionRecordRow
from qbet.web.display_preferences import DisplayPreferences, format_datetime, format_money
from qbet.web.models import UserDisplayPreference
from qbet.workflow.approval import ExecutionApprovalService
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

    def _stage_execution(
        self,
        *,
        owner: User | None = None,
        opportunity_id: str = "web-approval-opportunity",
        now: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> UUID:
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(bonus=EngineModes(execution=True)),
            queue_repository=ModeWorkQueueRepository(),
        )
        request = _request(opportunity_id)
        observed_at = now or datetime.now(UTC)
        approval_owner = owner or self.user
        (scheduled,) = coordinator.schedule(
            request,
            owner=approval_owner.get_username(),
            correlation_id=CORRELATION_ID,
            scheduled_for=observed_at,
            expires_at=expires_at or observed_at + timedelta(minutes=5),
        )
        (waiting,) = coordinator.dispatch_due(
            now=observed_at,
            owner=approval_owner.get_username(),
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
        self.assertContains(response, "Remaining")
        self.assertContains(response, "remaining")
        self.assertContains(response, 'class="nav-count">1</span>')
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
        self.assertNotContains(hidden, 'class="nav-count">1</span>')

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

    def test_awaiting_approval_repository_filters_owner_before_deserialization(self) -> None:
        self._stage_execution(owner=self.user)
        ExecutionRecordRow.objects.create(
            record_id=uuid4(),
            correlation_id=uuid4(),
            mode="execution",
            state=Lifecycle.AWAITING_APPROVAL.value,
            payload={"proposal": {"work": {"owner": self.other.get_username()}}},
        )

        records = ExecutionRecordRepository().list_awaiting_approval(
            owner=self.user.get_username()
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].proposal.work.owner, self.user.get_username())

    def test_execution_approval_uses_persisted_display_preferences(self) -> None:
        observed_at = datetime.now(UTC)
        execution_id = self._stage_execution(
            now=observed_at,
            expires_at=observed_at + timedelta(minutes=5),
        )
        UserDisplayPreference.objects.create(
            user=self.user,
            language="de",
            region="DE",
            timezone_name="Europe/Berlin",
            time_format="24h",
            currency="USD",
        )
        expected = ExecutionApprovalService().pending_for(
            self.user.get_username(),
            now=observed_at,
        )[0]
        display = DisplayPreferences(
            language="de",
            region="DE",
            timezone_name="Europe/Berlin",
            time_format="24h",
            currency="USD",
        )
        self.client.force_login(self.user)

        response = self.client.get("/execution/approvals/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            format_money(expected.capital_required, expected.currency, display),
        )
        self.assertContains(response, format_datetime(expected.expires_at, display))
        self.assertContains(response, "Preferred recorded currency: USD.")
        self.assertContains(response, "no FX conversion is applied.")
        self.assertContains(response, str(execution_id))
        self.assertNotContains(response, "Active")

    def test_expired_approval_get_is_hidden_without_mutating_execution_state(self) -> None:
        staged_at = datetime.now(UTC) - timedelta(minutes=10)
        execution_id = self._stage_execution(
            opportunity_id="expired-web-approval",
            now=staged_at,
            expires_at=staged_at + timedelta(minutes=5),
        )
        before = ExecutionStateRepository().load(execution_id)
        before_queue = ModeWorkQueueRepository().load(execution_id)
        assert before is not None and before_queue is not None
        self.client.force_login(self.user)

        response = self.client.get("/execution/approvals/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No pending approvals")
        self.assertNotContains(response, "expired-web-approval")
        self.assertNotContains(response, 'class="nav-count">1</span>')
        persisted = ExecutionStateRepository().load(execution_id)
        queue = ModeWorkQueueRepository().load(execution_id)
        assert persisted is not None and queue is not None
        record, ledger = persisted
        before_record, before_ledger = before
        self.assertEqual(record, before_record)
        self.assertEqual(record.state, Lifecycle.AWAITING_APPROVAL)
        self.assertEqual(queue, before_queue)
        self.assertEqual(queue.state, WorkState.RECHECK)
        self.assertEqual(ledger.commands, before_ledger.commands)

    def test_navigation_count_failure_does_not_break_authenticated_pages(self) -> None:
        self._stage_execution()
        self.client.force_login(self.user)

        with patch(
            "qbet.web.approval_context._APPROVALS.active_count_for",
            side_effect=DatabaseError("approval read unavailable"),
        ):
            response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")
        self.assertContains(response, "Approvals")
        self.assertNotContains(response, 'class="nav-count">1</span>')

    def test_inbox_read_failure_is_explicitly_unavailable(self) -> None:
        self.client.force_login(self.user)

        with patch(
            "qbet.web.execution_approvals._EXECUTION_APPROVALS.pending_for",
            side_effect=AuthoritativePersistenceError("approval state unavailable"),
        ):
            response = self.client.get("/execution/approvals/")

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Approvals temporarily unavailable", status_code=503)
        self.assertNotContains(response, "No pending approvals", status_code=503)

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
