"""One-time, time-limited email verification helpers."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils import timezone

from qbet.web.models import AccountVerification


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user: User, timestamp: int) -> str:
        return f"{user.pk}{user.password}{timestamp}{user.is_active}{user.email}"


email_verification_token = EmailVerificationTokenGenerator()


def remove_expired_unverified_accounts() -> int:
    """Remove accounts that never verified within the configured bounded window."""
    cutoff = timezone.now() - timedelta(seconds=settings.QBET_EMAIL_VERIFICATION_TIMEOUT)
    user_ids = AccountVerification.objects.filter(
        verified_at__isnull=True, created_at__lt=cutoff
    ).values_list("user_id", flat=True)
    deleted, _ = User.objects.filter(pk__in=user_ids, is_active=False).delete()
    return deleted