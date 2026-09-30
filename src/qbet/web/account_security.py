"""Account verification state and bounded cleanup helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.db import DatabaseError
from django.utils import timezone

from qbet.web.models import AccountVerification


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """Bind verification links to the current user, email, password, and active state."""

    def _make_hash_value(self, user: User, timestamp: int) -> str:
        return f"{user.pk}{user.password}{timestamp}{user.is_active}{user.email}"


email_verification_token = EmailVerificationTokenGenerator()


@dataclass(frozen=True)
class AccountVerificationStatus:
    verified: bool
    pending: bool
    expired: bool
    expires_at: datetime | None
    remaining_seconds: int | None


def account_verification_status(
    user: User,
    *,
    now: datetime | None = None,
) -> AccountVerificationStatus:
    """Project account verification without exposing token material."""

    current_time = now or timezone.now()
    try:
        verification = AccountVerification.objects.filter(user=user).first()
    except DatabaseError:
        verification = None

    if verification is None:
        return AccountVerificationStatus(
            verified=bool(user.is_active),
            pending=False,
            expired=False,
            expires_at=None,
            remaining_seconds=None,
        )

    expires_at = verification.created_at + timedelta(
        seconds=settings.QBET_EMAIL_VERIFICATION_TIMEOUT
    )
    verified = verification.verified_at is not None
    expired = not verified and current_time >= expires_at
    remaining_seconds = (
        None
        if verified
        else max(0, int((expires_at - current_time).total_seconds()))
    )
    return AccountVerificationStatus(
        verified=verified,
        pending=not verified and not expired,
        expired=expired,
        expires_at=expires_at,
        remaining_seconds=remaining_seconds,
    )


def remove_expired_unverified_accounts() -> int:
    """Delete inactive registrations after the bounded verification window."""

    cutoff = timezone.now() - timedelta(seconds=settings.QBET_EMAIL_VERIFICATION_TIMEOUT)
    try:
        user_ids = tuple(
            AccountVerification.objects.filter(
                verified_at__isnull=True,
                created_at__lt=cutoff,
                user__is_active=False,
            ).values_list("user_id", flat=True)
        )
        if not user_ids:
            return 0
        User.objects.filter(pk__in=user_ids, is_active=False).delete()
    except DatabaseError:
        return 0
    return len(user_ids)
