import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qbet.web.settings")

import django

django.setup()

from django.contrib.auth.models import User
from django.test import TestCase


class AuthenticationAndDashboardTests(TestCase):
    def test_registration_creates_normal_user_and_signs_in(self) -> None:
        response = self.client.post(
            "/register/",
            {"username": "new-user", "password1": "Strong-pass-123", "password2": "Strong-pass-123"},
        )

        user = User.objects.get(username="new-user")
        self.assertRedirects(response, "/dashboard/")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

    def test_invalid_registration_shows_form_error(self) -> None:
        response = self.client.post("/register/", {"username": "new-user"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "This field is required")

    def test_login_logout_and_dashboard_boundary(self) -> None:
        user = User.objects.create_user("member", password="Strong-pass-123")

        self.assertRedirects(self.client.get("/dashboard/"), "/accounts/login/?next=/dashboard/")
        response = self.client.post("/accounts/login/", {"username": user.username, "password": "Strong-pass-123"})
        self.assertRedirects(response, "/dashboard/")
        self.assertContains(self.client.get("/dashboard/"), "BonusEngine")
        self.assertContains(self.client.get("/dashboard/"), "SportsCapitalEngine")
        self.assertEqual(self.client.post("/accounts/logout/").status_code, 302)

    def test_admin_area_requires_staff_user(self) -> None:
        user = User.objects.create_user("member", password="Strong-pass-123")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin-area/").status_code, 302)

        staff = User.objects.create_user("staff", password="Strong-pass-123", is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/admin-area/").status_code, 200)
