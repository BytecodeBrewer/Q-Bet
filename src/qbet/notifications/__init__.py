"""Notification contracts, delivery adapters, and recipient readiness."""

from .email import CaptureEmailTransport, CapturedEmail, DjangoEmailTransport
from .models import (
    ExecutionNotificationInstruction,
    ExecutionNotificationTask,
    NotificationOutcome,
    NotificationRecipient,
    NotificationStatus,
)
from .recipients import NotificationRecipientStatus, notification_recipient_status
from .service import (
    ExecutionNotificationService,
    InMemoryNotificationRepository,
    NotificationDeliveryError,
    NotificationRepository,
    NotificationTransport,
)

__all__ = [
    "CaptureEmailTransport",
    "CapturedEmail",
    "DjangoEmailTransport",
    "ExecutionNotificationInstruction",
    "ExecutionNotificationService",
    "ExecutionNotificationTask",
    "InMemoryNotificationRepository",
    "NotificationDeliveryError",
    "NotificationOutcome",
    "NotificationRecipient",
    "NotificationRecipientStatus",
    "NotificationRepository",
    "NotificationStatus",
    "NotificationTransport",
    "notification_recipient_status",
]
