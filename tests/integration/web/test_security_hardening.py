from datetime import timedelta
from uuid import uuid4

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from qbet.web.account_security import email_verification_token
from qbet.web.models import AccountVerification


class WebSecurityHardeningTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            "security-user",
            password="Strong-pass-123",
            email="security-user@example.com",
        )
        self.staff = User.objects.create_user(
            "security-staff",
            password="Strong-pass-123",
            email="security-staff@example.com",
            is_staff=True,
        )

    def _verification_url(self, user: User) -> str:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = email_verification_token.make_token(user)
        return reverse("verify-email", args=(uid, token))

    def test_email_verification_get_is_read_only_and_post_requires_csrf(self) -> None:
        pending = User.objects.create_user(
            "pending-security-user",
            password="Strong-pass-123",
            email="pending@example.com",
            is_active=False,
        )
        AccountVerification.objects.create(user=pending)
        url = self._verification_url(pending)
        csrf_client = Client(enforce_csrf_checks=True)

        preview = csrf_client.get(url)

        pending.refresh_from_db()
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Confirm email")
        self.assertFalse(pending.is_active)
        self.assertIsNone(AccountVerification.objects.get(user=pending).verified_at)

        rejected = csrf_client.post(url)

        pending.refresh_from_db()
        self.assertEqual(rejected.status_code, 403)
        self.assertFalse(pending.is_active)

        accepted = csrf_client.post(
            url,
            {"csrfmiddlewaretoken": csrf_client.cookies["csrftoken"].value},
        )

        pending.refresh_from_db()
        verification = AccountVerification.objects.get(user=pending)
        self.assertRedirects(accepted, "/accounts/login/")
        self.assertTrue(pending.is_active)
        self.assertIsNotNone(verification.verified_at)

    def test_safe_account_get_does_not_delete_expired_registration(self) -> None:
        pending = User.objects.create_user(
            "expired-security-user",
            password="Strong-pass-123",
            email="expired@example.com",
            is_active=False,
        )
        verification = AccountVerification.objects.create(user=pending)
        AccountVerification.objects.filter(pk=verification.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )
        session = self.client.session
        session["pending_verification_user_id"] = pending.pk
        session.save()

        response = self.client.get("/verification/pending/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(pk=pending.pk, is_active=False).exists())

    def test_successful_login_rotates_pre_authentication_session_key(self) -> None:
        session = self.client.session
        session["pre_auth_marker"] = "rotate-me"
        session.save()
        before = session.session_key
        assert before is not None

        response = self.client.post(
            "/accounts/login/",
            {"username": self.user.username, "password": "Strong-pass-123"},
        )

        self.assertRedirects(response, "/dashboard/")
        after = self.client.session.session_key
        self.assertIsNotNone(after)
        self.assertNotEqual(after, before)

    def test_representative_state_changes_reject_get(self) -> None:
        self.client.force_login(self.user)
        user_routes = (
            "/dashboard/layout/",
            f"/notifications/{uuid4()}/read/",
            "/portfolio/central/refresh/",
        )
        for path in user_routes:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)

        self.client.force_login(self.staff)
        staff_routes = (
            "/simulation/start/",
            "/admin-area/gui-settings/simulation/",
            "/engines/sports_capital/simulation/start/",
            "/admin-area/sandbox-execution/sports_capital/start/",
        )
        for path in staff_routes:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)

    def test_csrf_blocks_representative_user_and_staff_mutations(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post("/dashboard/layout/", {}).status_code, 403)

        csrf_client.force_login(self.staff)
        self.assertEqual(
            csrf_client.post("/admin-area/gui-settings/simulation/", {"enabled": "on"}).status_code,
            403,
        )

    def test_normal_user_cannot_invoke_staff_runtime_control_directly(self) -> None:
        self.client.force_login(self.user)

        response = self.client.post("/engines/sports_capital/simulation/start/")

        self.assertEqual(response.status_code, 404)

    def test_correlation_scoped_provider_activity_is_staff_only(self) -> None:
        correlation = uuid4()
        self.client.force_login(self.user)

        generic = self.client.get("/activity/provider/")
        scoped = self.client.get("/activity/provider/", {"correlation": str(correlation)})

        self.assertEqual(generic.status_code, 200)
        self.assertEqual(scoped.status_code, 404)

        self.client.force_login(self.staff)
        staff_scoped = self.client.get(
            "/activity/provider/",
            {"correlation": str(correlation)},
        )
        self.assertEqual(staff_scoped.status_code, 200)

    def test_browser_responses_include_explicit_security_headers(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertIn("camera=()", response["Permissions-Policy"])
