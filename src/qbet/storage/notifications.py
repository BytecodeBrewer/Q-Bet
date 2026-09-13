"""PostgreSQL persistence for execution notification tasks."""

from __future__ import annotations

from uuid import UUID

from django.db import DatabaseError, IntegrityError, transaction

from qbet.notifications.models import ExecutionNotificationTask
from qbet.storage.models import NotificationTaskRow


class NotificationPersistenceError(RuntimeError):
    """Raised when durable notification state cannot be read or written."""


class PostgresNotificationRepository:
    def load(self, task_id: UUID) -> ExecutionNotificationTask | None:
        try:
            row = NotificationTaskRow.objects.filter(pk=task_id).first()
        except DatabaseError as error:
            raise NotificationPersistenceError("notification_state_unavailable") from error
        if row is None:
            return None
        return ExecutionNotificationTask.model_validate(row.payload)

    def create(
        self, task: ExecutionNotificationTask
    ) -> tuple[ExecutionNotificationTask, bool]:
        defaults = self._values(task)
        try:
            with transaction.atomic():
                row, created = NotificationTaskRow.objects.get_or_create(
                    task_id=task.id,
                    defaults=defaults,
                )
        except IntegrityError:
            try:
                row = NotificationTaskRow.objects.get(
                    execution_id=task.execution_id,
                    recipient_id=task.recipient.user_id,
                )
            except DatabaseError as error:
                raise NotificationPersistenceError("notification_state_unavailable") from error
            created = False
        except DatabaseError as error:
            raise NotificationPersistenceError("notification_state_unavailable") from error
        return ExecutionNotificationTask.model_validate(row.payload), created

    def save(self, task: ExecutionNotificationTask) -> ExecutionNotificationTask:
        try:
            updated = NotificationTaskRow.objects.filter(pk=task.id).update(**self._values(task))
        except DatabaseError as error:
            raise NotificationPersistenceError("notification_state_unavailable") from error
        if updated != 1:
            raise NotificationPersistenceError("notification_state_missing")
        return task

    @staticmethod
    def _values(task: ExecutionNotificationTask) -> dict[str, object]:
        return {
            "execution_id": task.execution_id,
            "correlation_id": task.correlation_id,
            "recipient_id": task.recipient.user_id,
            "state": task.status.value,
            "payload": task.model_dump(mode="json"),
        }
