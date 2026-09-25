from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from django.test import TestCase

from qbet.notifications.models import (
    ExecutionNotificationInstruction,
    ExecutionNotificationTask,
    NotificationRecipient,
    NotificationStatus,
)
from qbet.notifications.preferences import (
    NotificationPreferences,
    PostgresNotificationInbox,
    PostgresNotificationPreferenceRepository,
)
from qbet.storage.models import NotificationInboxDeliveryRow, NotificationTaskRow

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
TASK_ID = UUID("11111111-1111-1111-1111-111111111111")
EXECUTION_ID = UUID("22222222-2222-2222-2222-222222222222")
CORRELATION_ID = UUID("33333333-3333-3333-3333-333333333333")


def _task(*, user_id: str = "owner") -> ExecutionNotificationTask:
    return ExecutionNotificationTask(
        id=TASK_ID,
        execution_id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        recipient=NotificationRecipient(
            user_id=user_id,
            email="owner@example.test",
            display_name="Owner",
        ),
        opportunity_id="opportunity",
        engine="BonusEngine",
        strategy="qualifying_bet",
        instructions=(
            ExecutionNotificationInstruction(
                provider="bookmaker",
                offer_id="offer",
                amount="10",
                currency="EUR",
            ),
        ),
        action_starts_at=NOW,
        action_deadline=NOW + timedelta(minutes=30),
        created_at=NOW,
        lifecycle_at=NOW,
        status=NotificationStatus.SENT,
        sent_at=NOW,
        category="execution_action_required",
        email_requested=False,
        inbox_requested=True,
    )


def _persist(task: ExecutionNotificationTask) -> None:
    NotificationTaskRow.objects.create(
        task_id=task.id,
        execution_id=task.execution_id,
        correlation_id=task.correlation_id,
        recipient_id=task.recipient.user_id,
        state=task.status.value,
        payload=task.model_dump(mode="json"),
    )


class NotificationPreferenceInboxPersistenceTests(TestCase):
    def test_preferences_survive_repository_recreation(self) -> None:
        PostgresNotificationPreferenceRepository().save(
            "owner",
            NotificationPreferences(
                email_enabled=False,
                inbox_enabled=True,
                categories=("execution_action_required",),
            ),
        )

        restored = PostgresNotificationPreferenceRepository().load("owner")

        self.assertFalse(restored.email_enabled)
        self.assertTrue(restored.inbox_enabled)
        self.assertEqual(restored.categories, ("execution_action_required",))

    def test_inbox_delivery_is_owner_isolated_and_read_state_survives_recreation(self) -> None:
        task = _task()
        _persist(task)
        inbox = PostgresNotificationInbox()
        self.assertTrue(inbox.deliver("owner", task.id, task.category))

        owner_items = PostgresNotificationInbox().list(
            "owner", now=NOW + timedelta(minutes=1)
        )
        other_items = PostgresNotificationInbox().list(
            "other", now=NOW + timedelta(minutes=1)
        )

        self.assertEqual(len(owner_items), 1)
        self.assertTrue(owner_items[0].actionable)
        self.assertFalse(owner_items[0].read)
        self.assertEqual(other_items, ())
        self.assertFalse(PostgresNotificationInbox().mark_read("other", task.id))
        self.assertTrue(PostgresNotificationInbox().mark_read("owner", task.id))

        restored = PostgresNotificationInbox().list(
            "owner", now=NOW + timedelta(minutes=1)
        )
        self.assertTrue(restored[0].read)

    def test_unread_count_is_owner_scoped_and_ignores_stale_deliveries(self) -> None:
        task = _task()
        _persist(task)
        inbox = PostgresNotificationInbox()
        self.assertTrue(inbox.deliver("owner", task.id, task.category))
        NotificationInboxDeliveryRow.objects.create(
            user_id="owner",
            task_id=uuid4(),
            category="execution_action_required",
        )

        self.assertEqual(inbox.unread_count("owner"), 1)
        self.assertEqual(inbox.unread_count("other"), 0)

        self.assertTrue(inbox.mark_read("owner", task.id))
        self.assertEqual(inbox.unread_count("owner"), 0)

    def test_unread_count_bounds_recent_deliveries_before_read_filter(self) -> None:
        old_unread = _task().model_copy(
            update={"id": uuid4(), "execution_id": uuid4(), "correlation_id": uuid4()}
        )
        recent_read_a = _task().model_copy(
            update={"id": uuid4(), "execution_id": uuid4(), "correlation_id": uuid4()}
        )
        recent_read_b = _task().model_copy(
            update={"id": uuid4(), "execution_id": uuid4(), "correlation_id": uuid4()}
        )
        inbox = PostgresNotificationInbox()
        for task in (old_unread, recent_read_a, recent_read_b):
            _persist(task)
            self.assertTrue(inbox.deliver("owner", task.id, task.category))

        self.assertTrue(inbox.mark_read("owner", recent_read_a.id))
        self.assertTrue(inbox.mark_read("owner", recent_read_b.id))

        self.assertEqual(inbox.unread_count("owner", limit=2), 0)
        self.assertEqual(inbox.unread_count("owner", limit=3), 1)

    def test_suppressed_task_does_not_appear_after_preferences_are_reenabled(self) -> None:
        task = _task()
        _persist(task)
        preferences = PostgresNotificationPreferenceRepository()
        preferences.save(
            "owner",
            NotificationPreferences(
                email_enabled=False,
                inbox_enabled=False,
                categories=(),
            ),
        )

        self.assertEqual(PostgresNotificationInbox().list("owner", now=NOW), ())

        preferences.save("owner", NotificationPreferences())

        self.assertEqual(PostgresNotificationInbox().list("owner", now=NOW), ())
