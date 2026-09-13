from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from qbet.calculations import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.execution.models import ApprovedExecutionRequest, ExecutionProposal, ExecutionRecord, Lifecycle
from qbet.notifications import (
    CaptureEmailTransport,
    ExecutionNotificationService,
    InMemoryNotificationRepository,
    NotificationRecipient,
    NotificationStatus,
)
from qbet.workflow import WorkflowMode
from qbet.workflow.queue import QueuedWorkItem
from qbet.workflow.routing import RoutedWorkItem

NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
EXECUTION_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _approved() -> tuple[ExecutionRecord, QueuedWorkItem]:
    request = BonusEngineRequest(
        opportunity_id="expiry-opportunity",
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100"),
        ),
        currency="EUR",
        execution_offer_ids=("bookmaker:home", "exchange:home"),
        generated_at=NOW,
    )
    work = RoutedWorkItem(
        id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        engine="bonus",
        mode=WorkflowMode.EXECUTION,
        opportunity_id=request.opportunity_id,
        capital_context="owner:execution",
        owner="owner",
    )
    proposal = ExecutionProposal(
        work=work,
        request=request,
        expires_at=NOW + timedelta(minutes=2),
        currency="EUR",
        capital_required=Decimal("20"),
        payout=Decimal("21"),
    )
    approval = ApprovedExecutionRequest(
        proposal=proposal,
        approved_by="owner",
        approved_at=NOW,
    )
    record = ExecutionRecord(
        proposal=proposal,
        state=Lifecycle.APPROVED,
        transitions=(Lifecycle.PROPOSED, Lifecycle.AWAITING_APPROVAL, Lifecycle.APPROVED),
        approval=approval,
    )
    queued = QueuedWorkItem.pending(
        work,
        request,
        scheduled_for=NOW,
        expires_at=NOW + timedelta(minutes=2),
    )
    return record, queued


def test_late_acknowledgement_expires_sent_notification_without_resending() -> None:
    repository = InMemoryNotificationRepository()
    transport = CaptureEmailTransport()
    service = ExecutionNotificationService(repository=repository, transport=transport)
    record, queued = _approved()
    recipient = NotificationRecipient(
        user_id="owner",
        email="owner@example.com",
        display_name="Owner",
    )

    sent = service.notify(record, queued, recipient, now=NOW)
    assert sent.accepted and sent.task is not None
    expired = service.acknowledge(
        sent.task.id,
        recipient_id="owner",
        now=NOW + timedelta(minutes=2),
    )

    assert not expired.accepted
    assert expired.reason_code == "notification_expired"
    assert expired.task is not None
    assert expired.task.status is NotificationStatus.EXPIRED
    assert expired.task.sent_at == NOW
    assert len(transport.messages) == 1
