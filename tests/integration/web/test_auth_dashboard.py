from django.contrib.auth.models import User
from django.test import Client, TestCase

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

django.setup()


class AuthenticationAndDashboardTests(TestCase):
    def test_registration_creates_normal_user_and_signs_in(self) -> None:
        response = self.client.post(
            "/register/",
            {
                "username": "new-user",
                "password1": "Strong-pass-123",
                "password2": "Strong-pass-123",
            },
        )

        user = User.objects.get(username="new-user")
        self.assertRedirects(response, "/dashboard/")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_invalid_registration_shows_generic_error(self) -> None:
        response = self.client.post("/register/", {"username": "new-user"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration details were not accepted.")

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
        self.assertContains(dashboard, "No known issues")
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
        self.assertContains(response, "Open dashboard")
        self.assertContains(response, 'href="/dashboard/"')
        self.assertContains(response, 'href="/settings/presentation/"')
        self.assertNotContains(response, 'href="/monitoring/"')
        self.assertNotContains(response, 'href="/admin-area/"')
        self.assertNotContains(response, 'href="/admin/"')

    def test_staff_home_exposes_separate_operator_destinations(self) -> None:
        staff = User.objects.create_user(
            "staff-home",
            password="Strong-pass-123",
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Operator shortcuts")
        self.assertContains(response, 'href="/reports/"')
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
