from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from qbet.calculations import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.execution.models import (
    ApprovedExecutionRequest,
    ExecutionProposal,
    ExecutionRecord,
    Lifecycle,
)
from qbet.monitoring import MonitoringRecord
from qbet.notifications import (
    CaptureEmailTransport,
    ExecutionNotificationService,
    InMemoryNotificationRepository,
    NotificationPreferences,
    NotificationRecipient,
    NotificationStatus,
    PostgresNotificationInbox,
)
from qbet.storage.models import NotificationTaskRow
from qbet.workflow import WorkflowMode
from qbet.workflow.queue import QueuedWorkItem
from qbet.workflow.routing import RoutedWorkItem

NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
EXECUTION_ID = UUID("87654321-4321-8765-4321-876543218765")


class CaptureMonitoringWriter:
    def __init__(self) -> None:
        self.records: list[MonitoringRecord] = []

    def append(self, record: MonitoringRecord) -> MonitoringRecord:
        self.records.append(record)
        return record


def request() -> BonusEngineRequest:
    return BonusEngineRequest(
        opportunity_id="mail-opportunity",
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100"),
        ),
        currency="EUR",
        execution_offer_ids=("bookmaker", "exchange"),
        generated_at=NOW - timedelta(minutes=2),
    )


def approved_record(*, owner: str = "owner") -> tuple[ExecutionRecord, QueuedWorkItem]:
    item = request()
    work = RoutedWorkItem(
        id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        engine="bonus",
        mode=WorkflowMode.EXECUTION,
        opportunity_id=item.opportunity_id,
        capital_context=f"{owner}:execution",
        owner=owner,
    )
    proposal = ExecutionProposal(
        work=work,
        request=item,
        expires_at=NOW + timedelta(minutes=30),
        currency="EUR",
        capital_required=Decimal("20"),
        payout=Decimal("21"),
    )
    approval = ApprovedExecutionRequest(
        proposal=proposal,
        approved_by=owner,
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
        item,
        scheduled_for=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=30),
    )
    return record, queued


def recipient(**changes: str) -> NotificationRecipient:
    values = {
        "user_id": "owner",
        "email": "owner@example.com",
        "display_name": "Owner Example",
    }
    values.update(changes)
    return NotificationRecipient(**values)


def test_approved_assigned_execution_sends_one_idempotent_notification() -> None:
    repository = InMemoryNotificationRepository()
    transport = CaptureEmailTransport()
    monitoring = CaptureMonitoringWriter()
    service = ExecutionNotificationService(
        repository=repository,
        transport=transport,
        monitoring_writer=monitoring,
    )
    record, queued = approved_record()

    first = service.notify(record, queued, recipient(), now=NOW)
    repeated = service.notify(record, queued, recipient(), now=NOW + timedelta(seconds=1))

    assert first.accepted
    assert first.task is not None
    assert first.task.status is NotificationStatus.SENT
    assert first.task.strategy == "qualifying_bet"
    assert tuple(item.provider for item in first.task.instructions) == ("bookmaker", "exchange")
    assert tuple(item.amount for item in first.task.instructions) == (
        Decimal("10"),
        first.task.instructions[1].amount,
    )
    assert repeated.accepted and repeated.duplicate
    assert repeated.task == first.task
    assert len(transport.messages) == 1
    assert [record.status for record in monitoring.records] == ["queued", "sent"]
    serialized_refs = repr([record.references for record in monitoring.records])
    assert "owner@example.com" not in serialized_refs
    assert "token" not in serialized_refs.lower()


def test_unapproved_unassigned_and_expired_work_never_sends() -> None:
    transport = CaptureEmailTransport()
    repository = InMemoryNotificationRepository()
    service = ExecutionNotificationService(repository=repository, transport=transport)
    record, queued = approved_record()

    unapproved = record.model_copy(update={"state": Lifecycle.AWAITING_APPROVAL, "approval": None})
    assert (
        service.notify(unapproved, queued, recipient(), now=NOW).reason_code
        == "execution_not_approved"
    )
    unassigned = record.model_copy(
        update={
            "proposal": record.proposal.model_copy(
                update={"work": record.proposal.work.model_copy(update={"owner": None})}
            )
        }
    )
    assert (
        service.notify(unassigned, queued, recipient(), now=NOW).reason_code
        == "execution_not_assigned"
    )
    assert (
        service.notify(record, queued, recipient(), now=NOW + timedelta(minutes=30)).reason_code
        == "execution_expired"
    )
    assert not transport.messages


def test_notification_requires_a_twenty_to_fifty_minute_action_window() -> None:
    record, queued = approved_record()
    too_short_record = record.model_copy(
        update={
            "proposal": record.proposal.model_copy(
                update={"expires_at": NOW + timedelta(minutes=5)}
            )
        }
    )
    too_short = queued.model_copy(
        update={
            "scheduled_for": NOW,
            "expires_at": NOW + timedelta(minutes=5),
        }
    )
    outcome = ExecutionNotificationService(
        repository=InMemoryNotificationRepository(),
        transport=CaptureEmailTransport(),
    ).notify(too_short_record, too_short, recipient(), now=NOW)

    assert not outcome.accepted
    assert outcome.reason_code == "notification_action_window_invalid"


def test_notification_accepts_the_action_window_boundaries() -> None:
    record, queued = approved_record()
    service = ExecutionNotificationService(
        repository=InMemoryNotificationRepository(),
        transport=CaptureEmailTransport(),
    )

    twenty_record = record.model_copy(
        update={
            "proposal": record.proposal.model_copy(
                update={"expires_at": NOW + timedelta(minutes=20)}
            )
        }
    )
    twenty_minutes = queued.model_copy(
        update={
            "scheduled_for": NOW,
            "expires_at": NOW + timedelta(minutes=20),
        }
    )
    fifty_record = record.model_copy(
        update={
            "proposal": record.proposal.model_copy(
                update={"expires_at": NOW + timedelta(minutes=50)}
            )
        }
    )
    fifty_minutes = queued.model_copy(
        update={
            "scheduled_for": NOW,
            "expires_at": NOW + timedelta(minutes=50),
        }
    )

    assert service.notify(
        twenty_record, twenty_minutes, recipient(user_id="twenty"), now=NOW
    ).accepted
    assert service.notify(fifty_record, fifty_minutes, recipient(user_id="fifty"), now=NOW).accepted


def test_notification_rejects_an_action_window_longer_than_fifty_minutes() -> None:
    record, queued = approved_record()
    too_long_record = record.model_copy(
        update={
            "proposal": record.proposal.model_copy(
                update={"expires_at": NOW + timedelta(minutes=51)}
            )
        }
    )
    too_long = queued.model_copy(
        update={
            "scheduled_for": NOW,
            "expires_at": NOW + timedelta(minutes=51),
        }
    )

    outcome = ExecutionNotificationService(
        repository=InMemoryNotificationRepository(),
        transport=CaptureEmailTransport(),
    ).notify(too_long_record, too_long, recipient(), now=NOW)

    assert not outcome.accepted
    assert outcome.reason_code == "notification_action_window_invalid"


def test_missing_and_invalid_email_become_explicit_failed_states() -> None:
    record, queued = approved_record()
    for email, reason in (
        ("", "recipient_email_missing"),
        ("not-an-email", "recipient_email_invalid"),
    ):
        repository = InMemoryNotificationRepository()
        transport = CaptureEmailTransport()
        outcome = ExecutionNotificationService(
            repository=repository,
            transport=transport,
        ).notify(record, queued, recipient(email=email), now=NOW)

        assert not outcome.accepted
        assert outcome.reason_code == reason
        assert outcome.task is not None
        assert outcome.task.status is NotificationStatus.FAILED
        assert outcome.task.failure_reason == reason
        assert not transport.messages


def test_sent_notification_can_be_acknowledged_once_by_its_recipient() -> None:
    record, queued = approved_record()
    repository = InMemoryNotificationRepository()
    service = ExecutionNotificationService(
        repository=repository,
        transport=CaptureEmailTransport(),
    )
    sent = service.notify(record, queued, recipient(), now=NOW)
    assert sent.task is not None

    wrong_user = service.acknowledge(
        sent.task.id,
        recipient_id="other",
        now=NOW + timedelta(seconds=1),
    )
    first = service.acknowledge(
        sent.task.id,
        recipient_id="owner",
        now=NOW + timedelta(seconds=1),
    )
    repeated = service.acknowledge(
        sent.task.id,
        recipient_id="owner",
        now=NOW + timedelta(seconds=2),
    )

    assert wrong_user.reason_code == "notification_recipient_mismatch"
    assert first.accepted and first.task is not None
    assert first.task.status is NotificationStatus.ACKNOWLEDGED
    assert repeated.accepted and repeated.duplicate


class PreferenceRepository:
    def __init__(self, preferences: NotificationPreferences) -> None:
        self._preferences = preferences

    def load(self, user_id: str) -> NotificationPreferences:
        return self._preferences

    def save(self, user_id: str, preferences: NotificationPreferences) -> NotificationPreferences:
        self._preferences = preferences
        return preferences


class InboxDeliveryRepository:
    def __init__(self) -> None:
        self.deliveries: set[tuple[str, UUID, str]] = set()

    def deliver(self, user_id: str, task_id: UUID, category: str) -> bool:
        self.deliveries.add((user_id, task_id, category))
        return True


def test_inbox_delivery_decision_is_not_reinterpreted_after_preferences_change() -> None:
    preferences = PreferenceRepository(
        NotificationPreferences(email_enabled=False, inbox_enabled=False)
    )
    deliveries = InboxDeliveryRepository()
    repository = InMemoryNotificationRepository()
    record, queued = approved_record()
    service = ExecutionNotificationService(
        repository=repository,
        transport=CaptureEmailTransport(),
        preference_repository=preferences,
        inbox_delivery_repository=deliveries,
    )

    first = service.notify(record, queued, recipient(), now=NOW)
    preferences.save("owner", NotificationPreferences(email_enabled=False, inbox_enabled=True))
    repeated = service.notify(record, queued, recipient(), now=NOW + timedelta(seconds=1))

    assert first.accepted
    assert first.task is not None
    assert not first.task.inbox_requested
    assert repeated.duplicate
    assert repeated.task == first.task
    assert not deliveries.deliveries


def test_inbox_delivery_survives_invalid_email_when_email_channel_is_disabled() -> None:
    deliveries = InboxDeliveryRepository()
    record, queued = approved_record()
    outcome = ExecutionNotificationService(
        repository=InMemoryNotificationRepository(),
        transport=CaptureEmailTransport(),
        preference_repository=PreferenceRepository(
            NotificationPreferences(email_enabled=False, inbox_enabled=True)
        ),
        inbox_delivery_repository=deliveries,
    ).notify(record, queued, recipient(email=""), now=NOW)

    assert outcome.accepted
    assert outcome.task is not None
    assert outcome.task.status is NotificationStatus.SENT
    assert outcome.task.inbox_requested
    assert not outcome.task.email_requested
    assert ("owner", outcome.task.id, "execution_action_required") in deliveries.deliveries


def test_category_preference_suppresses_email_without_changing_execution_state() -> None:
    preferences = NotificationPreferences(categories=())
    transport = CaptureEmailTransport()
    record, queued = approved_record()
    outcome = ExecutionNotificationService(
        repository=InMemoryNotificationRepository(),
        transport=transport,
        preference_repository=PreferenceRepository(preferences),
    ).notify(record, queued, recipient(), now=NOW)

    assert outcome.accepted
    assert outcome.task is not None
    assert outcome.task.status is NotificationStatus.QUEUED
    assert not transport.messages
    assert record.state is Lifecycle.APPROVED


def test_inbox_projection_is_not_actionable_at_or_after_the_deadline() -> None:
    record, queued = approved_record()
    sent = ExecutionNotificationService(
        repository=InMemoryNotificationRepository(),
        transport=CaptureEmailTransport(),
    ).notify(record, queued, recipient(), now=NOW)
    assert sent.task is not None
    row = NotificationTaskRow(
        task_id=sent.task.id,
        execution_id=sent.task.execution_id,
        correlation_id=sent.task.correlation_id,
        recipient_id=sent.task.recipient.user_id,
        state=sent.task.status.value,
        payload=sent.task.model_dump(mode="json"),
    )

    before = PostgresNotificationInbox._item(
        row,
        category=sent.task.category,
        read=False,
        now=sent.task.action_deadline - timedelta(microseconds=1),
    )
    at_deadline = PostgresNotificationInbox._item(
        row,
        category=sent.task.category,
        read=False,
        now=sent.task.action_deadline,
    )
    after = PostgresNotificationInbox._item(
        row,
        category=sent.task.category,
        read=False,
        now=sent.task.action_deadline + timedelta(microseconds=1),
    )

    assert before.actionable
    assert before.link == "/execution/approvals/"
    assert not at_deadline.actionable
    assert not after.actionable
    assert at_deadline.link == "/inbox/"
