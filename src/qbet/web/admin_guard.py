from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser

_LAST_SUPERUSER_MESSAGE = "Q-Bet must retain at least one active staff superuser with admin access."


def is_active_admin(user: AbstractUser) -> bool:
    return bool(user.is_active and user.is_staff and user.is_superuser)


def has_other_active_admin(*, exclude_pk: object | None) -> bool:
    User = get_user_model()
    queryset = User.objects.filter(is_active=True, is_staff=True, is_superuser=True)
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.exists()


def last_superuser_message() -> str:
    return _LAST_SUPERUSER_MESSAGE
