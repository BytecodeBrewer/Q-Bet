"""Notification contracts and delivery adapters."""

from .email import CaptureEmailTransport, CapturedEmail, DjangoEmailTransport
from .models import (
    ExecutionNotificationInstruction,
    ExecutionNotificationTask,
    NotificationOutcome,
    NotificationRecipient,
    NotificationStatus,
)
from .recipients import (
    ActiveUserNotificationRecipientResolver,
    NotificationRecipientResolver,
)
from .service import (
    ExecutionNotificationService,
    InMemoryNotificationRepository,
    NotificationDeliveryError,
    NotificationRepository,
    NotificationTransport,
)

__all__ = [
    "ActiveUserNotificationRecipientResolver",
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
    "NotificationRecipientResolver",
    "NotificationRepository",
    "NotificationStatus",
    "NotificationTransport",
]
