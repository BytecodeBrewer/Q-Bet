from django.contrib.auth.models import User
from django.test import TestCase, override_settings


@override_settings(QBET_HOSTED_PREVIEW=True, QBET_SIMULATION_MODE_ENABLED=False)
class HostedPostgresTests(TestCase):
    def setUp(self) -> None:
        self.staff = User.objects.create_user(
            "hosted-staff",
            password="Strong-pass-123",
            is_staff=True,
        )

    def test_public_shell_health_and_auth_are_available(self) -> None:
        home = self.client.get("/")
        health = self.client.get("/health/")
        register = self.client.get("/register/")
        login = self.client.get("/accounts/login/")

        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "/static/qbet_web/app.css")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(register.status_code, 200)
        self.assertEqual(login.status_code, 200)

    def test_operational_routes_are_no_longer_blocked_by_hosted_boundary(self) -> None:
        for path in (
            "/dashboard/",
            "/reports/",
            "/monitoring/",
            "/admin-area/",
            "/admin/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNotEqual(response.status_code, 503)

    def test_staff_can_reach_postgres_backed_monitoring_and_reports(self) -> None:
        self.client.force_login(self.staff)

        self.assertEqual(self.client.get("/monitoring/").status_code, 200)
        self.assertEqual(self.client.get("/reports/").status_code, 200)
        self.assertEqual(self.client.get("/admin-area/").status_code, 200)
