"""Validated notification-recipient state and recipient resolution boundaries."""

from __future__ import annotations

from typing import Protocol

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from qbet.domain.models import DomainModel, Identifier

from .models import NotificationRecipient


class NotificationRecipientStatus(DomainModel):
    user_id: Identifier
    display_name: Identifier
    email: str = ""
    ready: bool
    reason_code: Identifier | None = None


def notification_recipient_status(
    *,
    user_id: str,
    email: str,
    first_name: str = "",
    last_name: str = "",
    display_name: str = "",
) -> NotificationRecipientStatus:
    """Return the canonical typed delivery-readiness projection for one recipient."""

    normalized_email = email.strip()
    normalized_display_name = display_name.strip()
    if not normalized_display_name:
        normalized_display_name = " ".join(
            part for part in (first_name.strip(), last_name.strip()) if part
        )
    if not normalized_display_name:
        normalized_display_name = user_id

    if not normalized_email:
        return NotificationRecipientStatus(
            user_id=user_id,
            display_name=normalized_display_name,
            ready=False,
            reason_code="recipient_email_missing",
        )
    try:
        validate_email(normalized_email)
    except ValidationError:
        return NotificationRecipientStatus(
            user_id=user_id,
            display_name=normalized_display_name,
            email=normalized_email,
            ready=False,
            reason_code="recipient_email_invalid",
        )
    return NotificationRecipientStatus(
        user_id=user_id,
        display_name=normalized_display_name,
        email=normalized_email,
        ready=True,
    )


class NotificationRecipientResolver(Protocol):
    """Resolves the audience for one shared execution opportunity."""

    def resolve(self) -> tuple[NotificationRecipient, ...]: ...


class ActiveUserNotificationRecipientResolver:
    """Returns every active Q-Bet user, including explicit invalid-email outcomes."""

    def resolve(self) -> tuple[NotificationRecipient, ...]:
        User = get_user_model()
        recipients: list[NotificationRecipient] = []
        for user in User.objects.filter(is_active=True).order_by("pk"):
            username = user.get_username() or str(user.pk)
            get_full_name = getattr(user, "get_full_name", None)
            full_name = str(get_full_name()).strip() if callable(get_full_name) else ""
            recipients.append(
                NotificationRecipient(
                    user_id=username,
                    email=str(getattr(user, "email", "") or "").strip(),
                    display_name=full_name or username,
                )
            )
        return tuple(recipients)
