"""Email transports for execution notifications."""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.template.loader import render_to_string

from .models import ExecutionNotificationTask
from .service import NotificationDeliveryError


@dataclass(frozen=True)
class CapturedEmail:
    recipient: str
    subject: str
    notification_id: str


class CaptureEmailTransport:
    """Deterministic no-network adapter used by tests and local composition."""

    def __init__(self) -> None:
        self.messages: list[CapturedEmail] = []

    def send(self, task: ExecutionNotificationTask) -> None:
        _validate_email(task.recipient.email)
        self.messages.append(
            CapturedEmail(
                recipient=task.recipient.email,
                subject=_subject(task),
                notification_id=str(task.id),
            )
        )


class DjangoEmailTransport:
    """Thin adapter over Django's configured mail backend."""

    def send(self, task: ExecutionNotificationTask) -> None:
        _validate_email(task.recipient.email)
        message = EmailMessage(
            subject=_subject(task),
            body=render_to_string(
                "qbet_web/email/execution_notification.txt",
                {"notification": task},
            ),
            to=[task.recipient.email],
        )
        delivered = message.send(fail_silently=False)
        if delivered != 1:
            raise NotificationDeliveryError("email_delivery_failed")


def _subject(task: ExecutionNotificationTask) -> str:
    return f"Q-Bet action required: {task.opportunity_id}"


def _validate_email(value: str) -> None:
    if not value.strip():
        raise NotificationDeliveryError("recipient_email_missing")
    try:
        validate_email(value)
    except ValidationError as error:
        raise NotificationDeliveryError("recipient_email_invalid") from error
