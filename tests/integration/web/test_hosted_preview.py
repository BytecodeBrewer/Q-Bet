from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from qbet.web.monitoring import MonitoringService

_HOSTED_PREVIEW_SETTINGS = {
    "QBET_HOSTED_PREVIEW": True,
    "QBET_HOSTED_DATABASE_ENABLED": False,
    "QBET_SIMULATION_MODE_ENABLED": False,
    "QBET_SIMULATION_REPORT_DB": None,
    "SESSION_ENGINE": "django.contrib.sessions.backends.signed_cookies",
}
_HOSTED_POSTGRES_SETTINGS = {
    **_HOSTED_PREVIEW_SETTINGS,
    "QBET_HOSTED_DATABASE_ENABLED": True,
}


class HostedPreviewBoundaryTests(SimpleTestCase):
    @override_settings(**_HOSTED_PREVIEW_SETTINGS)
    def test_public_shell_and_health_remain_available_without_persistence(self) -> None:
        with patch("qbet.web.views.MONITORING_SERVICE", MonitoringService()):
            home = self.client.get("/")
            health = self.client.get("/health/")

        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Q-Bet")
        self.assertContains(home, "/static/qbet_web/app.css")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok", "service": "q-bet-web"})

    @override_settings(**_HOSTED_PREVIEW_SETTINGS)
    def test_hosted_mode_without_postgres_stays_read_only(self) -> None:
        for path in (
            "/register/",
            "/accounts/login/",
            "/dashboard/",
            "/reports/",
            "/simulation/",
            "/admin-area/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertContains(
                    response,
                    "read-only until persistent cloud storage is configured",
                    status_code=503,
                )

    @override_settings(**_HOSTED_POSTGRES_SETTINGS)
    def test_postgres_backed_hosted_mode_exposes_auth_and_basic_shell(self) -> None:
        for path in ("/register/", "/accounts/login/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

        for path in ("/dashboard/", "/account/", "/settings/presentation/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 302)

    @override_settings(**_HOSTED_POSTGRES_SETTINGS)
    def test_postgres_backed_hosted_mode_keeps_unmigrated_routes_closed(self) -> None:
        for path in ("/reports/", "/simulation/", "/monitoring/", "/admin-area/", "/admin/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 503)
                self.assertContains(
                    response,
                    "persistence adapter is migrated to cloud storage",
                    status_code=503,
                )

    @override_settings(
        QBET_HOSTED_PREVIEW=False,
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    )
    def test_local_mode_does_not_apply_hosted_preview_boundary(self) -> None:
        response = self.client.get("/accounts/login/")

        self.assertEqual(response.status_code, 200)
