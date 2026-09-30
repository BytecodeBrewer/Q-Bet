import logging
import re
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from qbet.web.account_security import email_verification_token
from qbet.web.logging import AccountTokenRedactionFilter
from qbet.web.middleware import safe_request_path
from qbet.web.models import AccountVerification


def _path_from_email(body: str, prefix: str) -> str:
    match = re.search(rf"https?://[^\s]+({re.escape(prefix)}[^\s]+)", body)
    if match is None:
        raise AssertionError(f"email did not contain {prefix}")
    return urlparse(match.group(0)).path


class AccountSecurityTests(TestCase):
    def _register(self) -> None:
        response = self.client.post(
            "/register/",
            {
                "username": "verify-user",
                "first_name": "Verify",
                "last_name": "User",
                "email": "verify@example.com",
                "password1": "Valid-pass-12345",
                "password2": "Valid-pass-12345",
            },
        )
        self.assertRedirects(response, "/verification/pending/")

    def test_verification_link_is_time_limited_and_one_time(self) -> None:
        self._register()
        user = User.objects.get(username="verify-user")
        pending = self.client.get("/verification/pending/")
        self.assertContains(pending, "The link expires at")
        self.assertFalse(user.is_active)
        self.assertEqual(len(mail.outbox), 1)

        path = _path_from_email(mail.outbox[0].body, "/verify-email/")
        preview = self.client.get(path)
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Confirm email")
        user.refresh_from_db()
        self.assertFalse(user.is_active)

        verified = self.client.post(path)
        self.assertRedirects(verified, "/accounts/login/")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(AccountVerification.objects.get(user=user).verified_at)

        reused = self.client.get(path)
        self.assertRedirects(reused, "/verification/pending/")

    def test_expired_inactive_registration_cleanup_never_runs_on_get(self) -> None:
        user = User.objects.create_user(
            "expired-user",
            email="expired@example.com",
            password="Valid-pass-12345",
            is_active=False,
        )
        verification = AccountVerification.objects.create(user=user)
        AccountVerification.objects.filter(pk=verification.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )

        self.assertEqual(self.client.get("/verification/pending/").status_code, 200)
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

        self.client.post("/register/", {"username": ""})
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_verification_rejects_registration_after_24_hours(self) -> None:
        user = User.objects.create_user(
            "late-user",
            email="late@example.com",
            password="Valid-pass-12345",
            is_active=False,
        )
        verification = AccountVerification.objects.create(user=user)
        token = email_verification_token.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        AccountVerification.objects.filter(pk=verification.pk).update(
            created_at=timezone.now() - timedelta(hours=25)
        )

        path = f"/verify-email/{uid}/{token}/"
        response = self.client.get(path)

        self.assertRedirects(response, "/verification/pending/")
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

        self.client.post(path)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_verified_email_cannot_be_replaced_from_profile_without_reverification(self) -> None:
        user = User.objects.create_user(
            "verified-profile-user",
            first_name="Verified",
            last_name="User",
            email="verified@example.com",
            password="Valid-pass-12345",
        )
        AccountVerification.objects.create(user=user, verified_at=timezone.now())
        self.client.force_login(user)

        response = self.client.post(
            "/profile/",
            {
                "first_name": "Updated",
                "last_name": "User",
                "email": "replacement@example.com",
                "email_enabled": "on",
                "inbox_enabled": "on",
            },
        )

        self.assertRedirects(response, "/profile/")
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Updated")
        self.assertEqual(user.email, "verified@example.com")

        profile = self.client.get("/profile/")
        self.assertContains(profile, "Email verified")
        self.assertContains(
            profile,
            "Verified email changes require a separate verification flow.",
        )

        settings = self.client.get("/settings/presentation/")
        self.assertContains(settings, "verified@example.com")
        self.assertContains(settings, "Verified")
        self.assertNotContains(settings, "replacement@example.com")

    def test_authenticated_user_can_change_password(self) -> None:
        user = User.objects.create_user(
            "password-user",
            email="password@example.com",
            password="Valid-pass-12345",
        )
        self.client.force_login(user)

        response = self.client.post(
            "/accounts/password/change/",
            {
                "old_password": "Valid-pass-12345",
                "new_password1": "Changed-pass-67890",
                "new_password2": "Changed-pass-67890",
            },
        )

        self.assertRedirects(response, "/accounts/password/change/done/")
        user.refresh_from_db()
        self.assertTrue(user.check_password("Changed-pass-67890"))
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

    def test_password_reset_response_is_non_enumerating_and_token_is_one_time(self) -> None:
        user = User.objects.create_user(
            "reset-user",
            email="reset@example.com",
            password="Valid-pass-12345",
        )
        existing = self.client.post(
            "/accounts/password/reset/",
            {"email": "reset@example.com"},
        )
        self.assertEqual(len(mail.outbox), 1)
        unknown = self.client.post(
            "/accounts/password/reset/",
            {"email": "missing@example.com"},
        )

        self.assertEqual(existing["Location"], unknown["Location"])
        self.assertEqual(len(mail.outbox), 1)

        path = _path_from_email(mail.outbox[0].body, "/accounts/password/reset/")
        opened = self.client.get(path)
        self.assertEqual(opened.status_code, 302)
        confirm_path = opened["Location"]
        self.assertContains(self.client.get(confirm_path), "Choose a new password")

        completed = self.client.post(
            confirm_path,
            {
                "new_password1": "Reset-pass-67890",
                "new_password2": "Reset-pass-67890",
            },
        )
        self.assertRedirects(completed, "/accounts/password/reset/complete/")
        user.refresh_from_db()
        self.assertTrue(user.check_password("Reset-pass-67890"))
        self.assertContains(self.client.get(path), "invalid or has expired")

    def test_password_reset_token_expires_after_configured_day(self) -> None:
        user = User.objects.create_user(
            "timeout-user",
            email="timeout@example.com",
            password="Valid-pass-12345",
        )
        issued_at = timezone.now().replace(tzinfo=None)
        with patch.object(default_token_generator, "_now", return_value=issued_at):
            self.client.post("/accounts/password/reset/", {"email": user.email})
        path = _path_from_email(mail.outbox[0].body, "/accounts/password/reset/")

        with patch.object(
            default_token_generator,
            "_now",
            return_value=issued_at + timedelta(hours=25),
        ):
            response = self.client.get(path)

        self.assertContains(response, "invalid or has expired")

    def test_account_token_paths_are_redacted_before_logging(self) -> None:
        verification_path = safe_request_path("/verify-email/MQ/token-value/")
        reset_path = safe_request_path("/accounts/password/reset/MQ/token-value/")

        self.assertEqual(verification_path, "/verify-email/<redacted>/")
        self.assertEqual(reset_path, "/accounts/password/reset/<redacted>/")
        self.assertNotIn("token-value", verification_path + reset_path)


def test_django_request_log_filter_redacts_account_tokens() -> None:
    record = logging.LogRecord(
        "django.request",
        logging.ERROR,
        __file__,
        1,
        "Internal Server Error: /verify-email/MQ/highly-secret-token/",
        (),
        None,
    )

    AccountTokenRedactionFilter().filter(record)

    assert "highly-secret-token" not in record.getMessage()
    assert "/verify-email/<redacted>/" in record.getMessage()
