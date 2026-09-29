from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase

from qbet.web.display_preferences import DisplayPreferenceRepository
from qbet.web.models import AccountVerification, UserDisplayPreference

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

django.setup()


class AuthenticationAndDashboardTests(TestCase):
    def test_authenticated_shell_labels_exist_on_independent_render_paths(self) -> None:
        staff = User.objects.create_user("shell-staff", password="Strong-pass-123", is_staff=True)
        UserDisplayPreference.objects.create(
            user=staff,
            language="en",
            region="DE",
            timezone_name="Europe/Berlin",
            time_format="24h",
            currency="EUR",
        )
        self.client.force_login(staff)

        for path in (
            "/execution/approvals/",
            "/admin-area/gui-settings/",
            "/admin-area/polling/",
            "/accounts/password/change/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'aria-label="Application navigation"')
                self.assertContains(response, "Main Dashboard")
                self.assertContains(response, "Reports")
                self.assertContains(response, "Settings")
                self.assertContains(response, "Profile &amp; account")
                self.assertContains(response, "Sign out")

    def test_registration_creates_inactive_user_pending_email_verification(self) -> None:
        response = self.client.post(
            "/register/",
            {
                "username": "new-user",
                "first_name": "New",
                "last_name": "User",
                "email": "new-user@example.com",
                "password1": "Strong-pass-123",
                "password2": "Strong-pass-123",
            },
        )

        user = User.objects.get(username="new-user")
        self.assertRedirects(response, "/verification/pending/")
        self.assertFalse(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.email, "new-user@example.com")
        self.assertTrue(AccountVerification.objects.filter(user=user, verified_at=None).exists())
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertEqual(len(mail.outbox), 1)

    def test_invalid_registration_shows_generic_error(self) -> None:
        response = self.client.post("/register/", {"username": "new-user"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration details were not accepted.")

    def test_profile_requires_login_and_marks_missing_email_unavailable(self) -> None:
        self.assertRedirects(self.client.get("/profile/"), "/accounts/login/?next=/profile/")
        user = User.objects.create_user("profile-user", password="Strong-pass-123")
        self.client.force_login(user)

        response = self.client.get("/profile/")

        self.assertContains(response, "recipient_email_missing")

    def test_profile_validates_and_updates_notification_identity(self) -> None:
        user = User.objects.create_user("profile-user", password="Strong-pass-123")
        self.client.force_login(user)

        response = self.client.post(
            "/profile/",
            {"first_name": "Profile", "last_name": "User", "email": "profile@example.com"},
        )

        user.refresh_from_db()
        self.assertRedirects(response, "/profile/")
        self.assertEqual(
            (user.first_name, user.last_name, user.email),
            ("Profile", "User", "profile@example.com"),
        )

    def test_duplicate_registration_does_not_disclose_existing_account(self) -> None:
        User.objects.create_user("protected-user", password="Strong-pass-123")

        response = self.client.post(
            "/register/",
            {
                "username": "protected-user",
                "password1": "Strong-pass-123",
                "password2": "Strong-pass-123",
            },
        )

        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Registration details were not accepted.", content)
        self.assertNotIn("A user with that username already exists.", content)
        self.assertNotIn("protected-user", content)

    def test_weak_password_is_rejected_by_django_validators(self) -> None:
        response = self.client.post(
            "/register/",
            {"username": "new-user", "password1": "short", "password2": "short"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="new-user").exists())
        self.assertContains(response, "Registration details were not accepted.")

    def test_login_logout_and_dashboard_boundary(self) -> None:
        user = User.objects.create_user("member", password="Strong-pass-123")

        self.assertRedirects(self.client.get("/dashboard/"), "/accounts/login/?next=/dashboard/")
        response = self.client.post(
            "/accounts/login/",
            {"username": user.username, "password": "Strong-pass-123"},
        )
        self.assertRedirects(response, "/dashboard/")
        dashboard = self.client.get("/dashboard/")
        content = dashboard.content.decode()
        self.assertEqual(dashboard.wsgi_request.user, user)
        self.assertEqual(content.count('data-engine-widget="bonus"'), 1)
        self.assertEqual(content.count('data-engine-widget="sports_capital"'), 1)
        self.assertContains(dashboard, "Execution idle")
        self.assertContains(dashboard, "Engine status")
        self.assertContains(dashboard, "Bonus input needed")
        self.assertNotContains(dashboard, "Warnings / errors")
        self.assertContains(dashboard, "Inactive")
        self.assertNotContains(dashboard, "BaseEngine")
        self.assertNotContains(dashboard, "YieldEngine")
        self.assertNotContains(dashboard, "AlphaEngine")
        self.assertEqual(self.client.post("/accounts/logout/").status_code, 302)

    def test_authenticated_home_stays_available_and_hides_admin_navigation(self) -> None:
        user = User.objects.create_user("member-home", password="Strong-pass-123")
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open Dashboard")
        self.assertContains(response, 'href="/dashboard/"')
        self.assertContains(response, 'href="/settings/presentation/"')
        self.assertNotContains(response, "Active matches")
        self.assertNotContains(response, "Warnings / errors")
        self.assertNotContains(response, 'href="/monitoring/"')
        self.assertNotContains(response, 'href="/admin-area/"')
        self.assertNotContains(response, 'href="/admin/"')

    def test_staff_home_remains_presentation_first_with_admin_navigation_in_shell(self) -> None:
        staff = User.objects.create_user(
            "staff-home",
            password="Strong-pass-123",
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open Dashboard")
        self.assertNotContains(response, "Operator shortcuts")
        self.assertNotContains(response, "Active matches")
        self.assertNotContains(response, "Warnings / errors")
        self.assertContains(response, 'href="/reports/?mode=execution"')
        self.assertContains(response, 'href="/monitoring/"')
        self.assertContains(response, 'href="/admin-area/"')
        self.assertContains(response, 'href="/admin/"')

    def test_invalid_login_is_generic(self) -> None:
        User.objects.create_user("protected-user", password="Strong-pass-123")

        response = self.client.post(
            "/accounts/login/",
            {"username": "protected-user", "password": "wrong-password"},
        )

        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Sign-in details were not accepted.", content)

    def test_logout_requires_a_valid_csrf_token(self) -> None:
        user = User.objects.create_user("member", password="Strong-pass-123")
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(user)
        csrf_client.get("/dashboard/")

        rejected = csrf_client.post("/accounts/logout/")
        accepted = csrf_client.post(
            "/accounts/logout/",
            {"csrfmiddlewaretoken": csrf_client.cookies["csrftoken"].value},
        )

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(accepted.status_code, 302)

    def test_admin_area_requires_staff_user(self) -> None:
        user = User.objects.create_user("member", password="Strong-pass-123")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin-area/").status_code, 302)

        staff = User.objects.create_user("staff", password="Strong-pass-123", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/admin-area/").status_code, 200)

    def test_display_preferences_persist_per_user_and_do_not_cross_accounts(self) -> None:
        owner = User.objects.create_user("display-owner", password="Strong-pass-123")
        other = User.objects.create_user("display-other", password="Strong-pass-123")
        self.client.force_login(owner)

        response = self.client.post(
            "/settings/presentation/",
            {
                "theme": "dark",
                "font_size": "large",
                "language": "en",
                "region": "US",
                "timezone_name": "America/New_York",
                "time_format": "12h",
                "currency": "USD",
            },
        )

        self.assertRedirects(response, "/settings/presentation/")
        rendered = self.client.get("/settings/presentation/")
        self.assertContains(rendered, '<html lang="en"')
        self.assertContains(rendered, ">Notifications<", html=False)
        self.assertContains(rendered, 'title="Preferred recorded currency">USD</span>', html=False)
        self.assertNotContains(rendered, "Benachrichtigungen / Notifications")
        self.assertContains(rendered, "Preferred recorded currency")
        self.assertContains(rendered, "USD — recorded values only")
        self.assertContains(rendered, "does not convert amounts between currencies")
        stored = UserDisplayPreference.objects.get(user=owner)
        self.assertEqual(
            (
                stored.language,
                stored.region,
                stored.timezone_name,
                stored.time_format,
                stored.currency,
            ),
            ("en", "US", "America/New_York", "12h", "USD"),
        )
        self.assertEqual(
            DisplayPreferenceRepository().load(other).currency,
            "EUR",
        )

    def test_invalid_persisted_display_preferences_fall_back_without_error(self) -> None:
        user = User.objects.create_user("legacy-display", password="Strong-pass-123")
        UserDisplayPreference.objects.create(
            user=user,
            language="xx",
            region="ZZ",
            timezone_name="Invalid/Zone",
            time_format="bad",
            currency="XXX",
        )
        self.client.force_login(user)

        response = self.client.get("/settings/presentation/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="de" selected')
        self.assertContains(response, 'value="Europe/Berlin" selected')
        self.assertContains(response, "Benachrichtigungen / Notifications")
