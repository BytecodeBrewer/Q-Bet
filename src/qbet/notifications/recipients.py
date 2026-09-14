"""Recipient resolution for the shared Phase 3 execution prototype."""

from __future__ import annotations

from typing import Protocol

from django.contrib.auth import get_user_model

from .models import NotificationRecipient


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
