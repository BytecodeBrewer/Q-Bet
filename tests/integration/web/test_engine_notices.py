from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from qbet.web.engine_notices import BonusInputSnapshot
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
        bonus_input: BonusInputSnapshot | None = None,
    ):
        activity = provider_activity or ProviderActivitySnapshot(
            state="ready",
            label="Market data ready; no query running.",
        )
        bonus = bonus_input or BonusInputSnapshot()
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
            patch(
                "qbet.web.views.bonus_input_snapshot",
                return_value=bonus,
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

    def test_dashboard_keeps_low_bonus_offer_counts_off_engine_card(self) -> None:
        self.client.force_login(self.user)

        response = self._dashboard(
            bonus_input=BonusInputSnapshot(
                active_offers=3,
                ready_offers=3,
                provider_count=2,
                coverage_state="limited",
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Bonus coverage")
        self.assertNotContains(response, "usable active offer")
        self.assertNotContains(response, "Add promotion data")
        self.assertNotContains(response, 'data-notice-reason="bonus_coverage_limited"')

    def test_engine_detail_does_not_turn_missing_offers_into_persistent_warning(self) -> None:
        self.client.force_login(self.user)

        response = self._engine_detail()

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Bonus input needed")
        self.assertNotContains(response, "Bonus coverage")
        self.assertNotContains(response, "<dt>Warnings</dt>", html=True)
        self.assertNotContains(response, "<dt>Errors</dt>", html=True)

    def test_dashboard_keeps_actual_bonus_input_read_failure_as_error(self) -> None:
        self.client.force_login(self.user)

        response = self._dashboard(
            bonus_input=BonusInputSnapshot(available=False),
        )

        self.assertContains(response, "Bonus input unavailable")
        self.assertContains(response, 'data-notice-reason="bonus_input_unavailable"')

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
                self.assertNotContains(response, "the_odds_api")

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
                self.assertNotContains(response, "the_odds_api")

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
