from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from qbet.web.provider_activity import ProviderActivitySnapshot
from qbet.workflow.routing import RoutingConfiguration


class ContextualEngineNoticeWebTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            "notice-user",
            password="Strong-pass-123",
        )

    def _dashboard(
        self,
        provider_activity: ProviderActivitySnapshot | None = None,
    ):
        activity = provider_activity or ProviderActivitySnapshot(
            state="ready",
            label="Market data ready; no query running.",
        )
        with (
            patch(
                "qbet.web.views._routing_configuration",
                return_value=(RoutingConfiguration(), True),
            ),
            patch(
                "qbet.web.views._execution_runtime_activity",
                return_value=({}, True),
            ),
            patch(
                "qbet.web.views._provider_activity",
                return_value=activity,
            ),
        ):
            return self.client.get("/dashboard/")

    def _engine_detail(self):
        with (
            patch(
                "qbet.web.views._routing_configuration",
                return_value=(RoutingConfiguration(), True),
            ),
            patch(
                "qbet.web.views._execution_runtime_activity",
                return_value=({}, True),
            ),
        ):
            return self.client.get("/engines/bonus/")

    def test_normal_user_dashboard_explains_missing_bonus_input_without_counters(self) -> None:
        self.client.force_login(self.user)

        response = self._dashboard()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bonus input needed")
        self.assertContains(response, "No active Bonus Offers are available for BonusEngine.")
        self.assertContains(response, 'data-notice-reason="bonus_offer_missing"')
        self.assertContains(response, 'href="/bonus-offers/"')
        self.assertNotContains(response, "Warnings / errors")
        self.assertNotContains(response, "Technical history:")

    def test_unresolved_notice_returns_after_page_reload(self) -> None:
        self.client.force_login(self.user)

        first = self._dashboard()
        second = self._dashboard()

        self.assertContains(first, 'data-notice-reason="bonus_offer_missing"')
        self.assertContains(second, 'data-notice-reason="bonus_offer_missing"')

    def test_engine_detail_uses_contextual_notice_instead_of_warning_error_metrics(self) -> None:
        self.client.force_login(self.user)

        response = self._engine_detail()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current notices")
        self.assertContains(response, "Bonus input needed")
        self.assertNotContains(response, "<dt>Warnings</dt>", html=True)
        self.assertNotContains(response, "<dt>Errors</dt>", html=True)
        self.assertNotContains(response, "Technical history:")

    def test_dashboard_renders_working_and_success_provider_activity_notices(self) -> None:
        self.client.force_login(self.user)

        cases = (
            (
                ProviderActivitySnapshot(
                    state="working",
                    label="Market data is updating.",
                    provider="the_odds_api",
                    reason_code="provider_query_started",
                ),
                "Market data is being refreshed",
                "Info · Market data",
            ),
            (
                ProviderActivitySnapshot(
                    state="success",
                    label="Market data updated successfully.",
                    provider="the_odds_api",
                    reason_code="provider_query_completed",
                ),
                "Market data updated",
                "Success · Market data",
            ),
        )

        for activity, title, level in cases:
            with self.subTest(state=activity.state):
                response = self._dashboard(activity)
                self.assertContains(response, "data-provider-notice")
                self.assertContains(response, title)
                self.assertContains(response, level)
                self.assertContains(response, "the_odds_api")

    def test_dashboard_renders_delayed_and_unavailable_provider_activity_notices(self) -> None:
        self.client.force_login(self.user)

        cases = (
            (
                ProviderActivitySnapshot(
                    state="delayed",
                    label="Market data update is delayed.",
                    provider="the_odds_api",
                    reason_code="provider_rate_limited",
                ),
                "Market data refresh is delayed",
                "Warning · Market data",
            ),
            (
                ProviderActivitySnapshot(
                    state="unavailable",
                    label="Market data source unavailable.",
                    provider="the_odds_api",
                    reason_code="provider_unavailable",
                ),
                "Market data source unavailable",
                "Error · Market data",
            ),
        )

        for activity, title, level in cases:
            with self.subTest(state=activity.state):
                response = self._dashboard(activity)
                self.assertContains(response, "data-provider-notice")
                self.assertContains(response, title)
                self.assertContains(response, level)
                self.assertContains(response, "the_odds_api")

    def test_ready_provider_activity_does_not_invent_notice(self) -> None:
        self.client.force_login(self.user)

        response = self._dashboard()

        self.assertNotContains(response, "data-provider-notice")

    def test_notice_dismissal_script_is_presentation_only(self) -> None:
        script = Path(
            settings.BASE_DIR,
            "static",
            "qbet_web",
            "engine_notices.js",
        ).read_text(encoding="utf-8")

        self.assertIn('target.closest("[data-notice-dismiss]")', script)
        self.assertIn("notice.remove()", script)
        self.assertNotIn("fetch(", script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("sessionStorage", script)
