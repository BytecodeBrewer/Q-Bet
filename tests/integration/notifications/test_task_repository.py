from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from django.test import TestCase

from qbet.notifications.models import (
    ExecutionNotificationInstruction,
    ExecutionNotificationTask,
    NotificationRecipient,
    NotificationStatus,
)
from qbet.storage.notifications import (
    NotificationPersistenceError,
    PostgresNotificationRepository,
)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
EXECUTION_ID = UUID("22222222-2222-2222-2222-222222222222")
CORRELATION_ID = UUID("33333333-3333-3333-3333-333333333333")


def notification_task(
    *,
    task_id: UUID | None = None,
    execution_id: UUID = EXECUTION_ID,
    recipient_id: str = "owner",
) -> ExecutionNotificationTask:
    return ExecutionNotificationTask(
        id=task_id or uuid4(),
        execution_id=execution_id,
        correlation_id=CORRELATION_ID,
        recipient=NotificationRecipient(
            user_id=recipient_id,
            email=f"{recipient_id}@example.test",
            display_name=recipient_id.title(),
        ),
        opportunity_id="notification-repository-opportunity",
        engine="BonusEngine",
        strategy="qualifying_bet",
        instructions=(
            ExecutionNotificationInstruction(
                provider="bookmaker",
                offer_id="offer",
                amount=Decimal("10"),
                currency="EUR",
            ),
        ),
        action_starts_at=NOW,
        action_deadline=NOW + timedelta(minutes=30),
        created_at=NOW,
        lifecycle_at=NOW,
    )


class PostgresNotificationRepositoryTests(TestCase):
    def test_create_load_and_save_round_trip_durable_task_state(self) -> None:
        repository = PostgresNotificationRepository()
        task = notification_task()

        created, was_created = repository.create(task)
        loaded = repository.load(task.id)

        self.assertTrue(was_created)
        self.assertEqual(created, task)
        self.assertEqual(loaded, task)

        queued = task.model_copy(
            update={
                "status": NotificationStatus.QUEUED,
                "lifecycle_at": NOW + timedelta(seconds=1),
            }
        )
        repository.save(queued)

        self.assertEqual(repository.load(task.id), queued)

    def test_execution_recipient_identity_is_idempotent_across_task_ids(self) -> None:
        repository = PostgresNotificationRepository()
        first = notification_task(task_id=uuid4())
        competing = notification_task(task_id=uuid4())

        persisted, first_created = repository.create(first)
        duplicate, duplicate_created = repository.create(competing)

        self.assertTrue(first_created)
        self.assertEqual(persisted, first)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate, first)
        self.assertIsNone(repository.load(competing.id))

    def test_save_missing_task_fails_closed(self) -> None:
        repository = PostgresNotificationRepository()

        with self.assertRaisesMessage(
            NotificationPersistenceError,
            "notification_state_missing",
        ):
            repository.save(notification_task())
