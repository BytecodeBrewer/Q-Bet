"""Validated notification-recipient state independent of any delivery transport."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from qbet.domain.models import DomainModel, Identifier


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
