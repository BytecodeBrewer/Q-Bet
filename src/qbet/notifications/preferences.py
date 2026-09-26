"""Durable notification delivery preferences and customer-safe inbox projections."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from django.db import DatabaseError
from django.db.models import Exists, OuterRef, Subquery
from django.utils import timezone

from qbet.notifications.models import ExecutionNotificationTask, NotificationStatus
from qbet.storage.models import (
    NotificationInboxDeliveryRow,
    NotificationInboxReadRow,
    NotificationPreferenceRow,
    NotificationTaskRow,
)

NOTIFICATION_CATEGORIES = (
    "execution_action_required",
    "approval_status",
    "funding_attention",
    "business_warning",
)


@dataclass(frozen=True)
class NotificationPreferences:
    email_enabled: bool = True
    inbox_enabled: bool = True
    categories: tuple[str, ...] = NOTIFICATION_CATEGORIES

    def accepts(self, category: str) -> bool:
        return category in self.categories


class NotificationPreferenceRepository(Protocol):
    def load(self, user_id: str) -> NotificationPreferences: ...
    def save(
        self, user_id: str, preferences: NotificationPreferences
    ) -> NotificationPreferences: ...


class NotificationInboxDeliveryRepository(Protocol):
    def deliver(self, user_id: str, task_id: UUID, category: str) -> bool: ...


class PostgresNotificationPreferenceRepository:
    def load(self, user_id: str) -> NotificationPreferences:
        try:
            row = NotificationPreferenceRow.objects.filter(user_id=user_id).first()
        except DatabaseError:
            return NotificationPreferences()
        if row is None:
            return NotificationPreferences()
        return NotificationPreferences(
            email_enabled=row.email_enabled,
            inbox_enabled=row.inbox_enabled,
            categories=tuple(row.categories),
        )

    def save(self, user_id: str, preferences: NotificationPreferences) -> NotificationPreferences:
        try:
            NotificationPreferenceRow.objects.update_or_create(
                user_id=user_id,
                defaults={
                    "email_enabled": preferences.email_enabled,
                    "inbox_enabled": preferences.inbox_enabled,
                    "categories": list(preferences.categories),
                },
            )
        except DatabaseError as error:
            raise RuntimeError("notification_preferences_unavailable") from error
        return preferences


@dataclass(frozen=True)
class InboxItem:
    task_id: UUID
    category: str
    title: str
    message: str
    link: str
    occurred_at: datetime
    actionable: bool
    read: bool


class PostgresNotificationInbox:
    """Durable event-time inbox delivery plus per-user read markers."""

    def deliver(self, user_id: str, task_id: UUID, category: str) -> bool:
        try:
            if not NotificationTaskRow.objects.filter(
                task_id=task_id, recipient_id=user_id
            ).exists():
                return False
            row, _ = NotificationInboxDeliveryRow.objects.get_or_create(
                user_id=user_id,
                task_id=task_id,
                defaults={"category": category},
            )
        except DatabaseError:
            return False
        return row.category == category

    def list(
        self,
        user_id: str,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> tuple[InboxItem, ...]:
        bounded_limit = max(1, min(limit, 100))
        current_time = now or timezone.now()
        try:
            deliveries = list(
                NotificationInboxDeliveryRow.objects.filter(user_id=user_id).order_by(
                    "-created_at", "-id"
                )[:bounded_limit]
            )
            task_ids = [delivery.task_id for delivery in deliveries]
            rows = {
                row.task_id: row
                for row in NotificationTaskRow.objects.filter(
                    task_id__in=task_ids,
                    recipient_id=user_id,
                )
            }
            reads = set(
                NotificationInboxReadRow.objects.filter(
                    user_id=user_id,
                    task_id__in=task_ids,
                ).values_list("task_id", flat=True)
            )
        except DatabaseError:
            return ()
        return tuple(
            self._item(
                row,
                category=delivery.category,
                read=delivery.task_id in reads,
                now=current_time,
            )
            for delivery in deliveries
            if (row := rows.get(delivery.task_id)) is not None
        )

    def unread_count(self, user_id: str, *, limit: int = 100) -> int:
        """Count recent valid unread deliveries without materializing inbox payloads."""

        bounded_limit = max(1, min(limit, 100))
        recent_delivery_ids = (
            NotificationInboxDeliveryRow.objects.filter(user_id=user_id)
            .order_by("-created_at", "-id")
            .values("id")[:bounded_limit]
        )
        owned_task = NotificationTaskRow.objects.filter(
            task_id=OuterRef("task_id"),
            recipient_id=user_id,
        )
        read_marker = NotificationInboxReadRow.objects.filter(
            user_id=user_id,
            task_id=OuterRef("task_id"),
        )
        try:
            return (
                NotificationInboxDeliveryRow.objects.filter(
                    user_id=user_id,
                    id__in=Subquery(recent_delivery_ids),
                )
                .annotate(
                    owned_task=Exists(owned_task),
                    read_marker=Exists(read_marker),
                )
                .filter(owned_task=True, read_marker=False)
                .count()
            )
        except DatabaseError:
            return 0

    def mark_read(self, user_id: str, task_id: UUID) -> bool:
        try:
            if not NotificationInboxDeliveryRow.objects.filter(
                user_id=user_id,
                task_id=task_id,
            ).exists():
                return False
            NotificationInboxReadRow.objects.get_or_create(user_id=user_id, task_id=task_id)
        except DatabaseError:
            return False
        return True

    @staticmethod
    def _item(
        row: NotificationTaskRow,
        *,
        category: str,
        read: bool,
        now: datetime,
    ) -> InboxItem:
        task = ExecutionNotificationTask.model_validate(row.payload)
        actionable = (
            task.status in {NotificationStatus.SENT, NotificationStatus.QUEUED}
            and now < task.action_deadline
        )
        title = "Execution action required" if actionable else "Execution notification update"
        message = f"{task.engine}: {task.strategy} for {task.opportunity_id}."
        return InboxItem(
            task_id=task.id,
            category=category,
            title=title,
            message=message,
            link="/execution/approvals/" if actionable else "/inbox/",
            occurred_at=task.lifecycle_at,
            actionable=actionable,
            read=read,
        )
