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
        self.smoke = User.objects.create_user(
            "qbet-preview-smoke",
            password=None,
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

    @override_settings(QBET_PREVIEW_SMOKE_TOKEN="preview-smoke-token")
    def test_preview_smoke_session_exercises_normal_authenticated_routes(self) -> None:
        created = self.client.post(
            "/internal/preview-smoke/session/",
            HTTP_AUTHORIZATION="Bearer preview-smoke-token",
        )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json(), {"status": "ok"})
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.smoke.pk)
        self.assertLessEqual(self.client.session.get_expiry_age(), 300)

        for path in ("/dashboard/", "/reports/", "/portfolio/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        cleared = self.client.delete(
            "/internal/preview-smoke/session/",
            HTTP_AUTHORIZATION="Bearer preview-smoke-token",
        )

        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json(), {"status": "cleared"})
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(QBET_PREVIEW_SMOKE_TOKEN="preview-smoke-token")
    def test_preview_smoke_session_hides_wrong_token_and_rejects_privileged_identity(self) -> None:
        hidden = self.client.post(
            "/internal/preview-smoke/session/",
            HTTP_AUTHORIZATION="Bearer wrong-token",
        )
        self.assertEqual(hidden.status_code, 404)

        self.smoke.is_staff = True
        self.smoke.save(update_fields=("is_staff",))
        rejected = self.client.post(
            "/internal/preview-smoke/session/",
            HTTP_AUTHORIZATION="Bearer preview-smoke-token",
        )

        self.assertEqual(rejected.status_code, 503)
        self.assertEqual(rejected.json()["reason"], "preview_smoke_identity_invalid")

    @override_settings(
        QBET_HOSTED_PREVIEW=False,
        QBET_PREVIEW_SMOKE_TOKEN="preview-smoke-token",
    )
    def test_preview_smoke_session_is_not_available_outside_preview(self) -> None:
        response = self.client.post(
            "/internal/preview-smoke/session/",
            HTTP_AUTHORIZATION="Bearer preview-smoke-token",
        )

        self.assertEqual(response.status_code, 404)
