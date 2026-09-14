"""Notification contracts and delivery adapters."""

from .email import CaptureEmailTransport, CapturedEmail, DjangoEmailTransport
from .models import (
    ExecutionNotificationInstruction,
    ExecutionNotificationTask,
    NotificationOutcome,
    NotificationRecipient,
    NotificationStatus,
)
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
    "NotificationRepository",
    "NotificationStatus",
    "NotificationTransport",
]
