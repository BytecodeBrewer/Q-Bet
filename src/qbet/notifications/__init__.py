"""Notification contracts, delivery adapters, recipient readiness, and resolution."""

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
    NotificationRecipientStatus,
    notification_recipient_status,
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
    "NotificationRecipientStatus",
    "NotificationRepository",
    "NotificationStatus",
    "NotificationTransport",
    "notification_recipient_status",
]
