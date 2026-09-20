from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.data.polling import PollingTarget
from qbet.storage.polling import PollingStrategyPersistenceError, PollingStrategyRepository


class PollingSettingsTests(TestCase):
    def setUp(self) -> None:
        self.staff = User.objects.create_user(
            "polling-staff",
            password="Strong-pass-123",
            is_staff=True,
        )
        self.user = User.objects.create_user(
            "polling-user",
            password="Strong-pass-123",
        )

    @staticmethod
    def market_payload(**changes: str) -> dict[str, str]:
        payload = {
            "provider_id": "provider-a",
            "source_id": "odds",
            "transport": "api",
            "target": "market",
            "engine": "sports_capital",
            "enabled": "on",
            "freshness_minutes": "5",
            "market_refresh_points_minutes": "1440,720,60",
            "market_interval_minutes": "",
            "latest_market_poll_before_event_minutes": "1",
            "result_retry_minutes": "10",
            "max_attempts": "3",
            "capacity_class": "free",
            "capacity_units": "100",
            "request_cost_units": "1",
        }
        payload.update(changes)
        return payload

    def test_polling_settings_are_staff_only(self) -> None:
        self.assertEqual(self.client.get("/admin-area/polling/").status_code, 302)

        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/admin-area/polling/").status_code, 302)
        self.assertEqual(
            self.client.post("/admin-area/polling/", self.market_payload()).status_code,
            302,
        )
        self.assertEqual(PollingStrategyRepository().list(), ())

    def test_staff_can_create_and_update_provider_engine_strategy(self) -> None:
        self.client.force_login(self.staff)

        created = self.client.post("/admin-area/polling/", self.market_payload())
        self.assertRedirects(created, "/admin-area/polling/")

        repository = PollingStrategyRepository()
        strategy = repository.load(
            provider_id="provider-a",
            source_id="odds",
            target=PollingTarget.MARKET,
            engine="sports_capital",
        )
        self.assertIsNotNone(strategy)
        assert strategy is not None
        self.assertEqual(len(strategy.market_refresh_points), 3)
        self.assertEqual(strategy.capacity_units, 100)

        updated = self.client.post(
            "/admin-area/polling/",
            self.market_payload(enabled="", capacity_units="0"),
        )
        self.assertRedirects(updated, "/admin-area/polling/")
        reloaded = repository.load(
            provider_id="provider-a",
            source_id="odds",
            target=PollingTarget.MARKET,
            engine="sports_capital",
        )
        self.assertIsNotNone(reloaded)
        assert reloaded is not None
        self.assertFalse(reloaded.enabled)
        self.assertEqual(reloaded.capacity_units, 0)

    def test_staff_can_toggle_persisted_strategy_without_recreating_it(self) -> None:
        repository = PollingStrategyRepository()
        self.client.force_login(self.staff)
        self.client.post("/admin-area/polling/", self.market_payload())

        response = self.client.post(
            "/admin-area/polling/",
            {
                "action": "toggle",
                "provider_id": "provider-a",
                "source_id": "odds",
                "target": "market",
                "engine": "sports_capital",
                "enabled": "false",
            },
        )

        self.assertRedirects(response, "/admin-area/polling/")
        strategy = repository.load(
            provider_id="provider-a",
            source_id="odds",
            target=PollingTarget.MARKET,
            engine="sports_capital",
        )
        self.assertIsNotNone(strategy)
        assert strategy is not None
        self.assertFalse(strategy.enabled)

    def test_invalid_or_unsupported_configuration_is_rejected(self) -> None:
        self.client.force_login(self.staff)

        unordered = self.client.post(
            "/admin-area/polling/",
            self.market_payload(market_refresh_points_minutes="60,720"),
        )
        self.assertEqual(unordered.status_code, 400)
        self.assertEqual(PollingStrategyRepository().list(), ())

        unexpected = self.market_payload()
        unexpected["api_key"] = "never-accepted"
        unsupported = self.client.post("/admin-area/polling/", unexpected)
        self.assertEqual(unsupported.status_code, 400)
        self.assertNotContains(unsupported, "never-accepted", status_code=400)
        self.assertEqual(PollingStrategyRepository().list(), ())

    def test_save_failure_keeps_safe_error_surface(self) -> None:
        self.client.force_login(self.staff)
        with patch(
            "qbet.web.polling_settings.PollingStrategyRepository.save",
            side_effect=PollingStrategyPersistenceError("database detail"),
        ):
            response = self.client.post(
                "/admin-area/polling/",
                self.market_payload(),
            )

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response,
            "Polling strategy configuration is temporarily unavailable",
            status_code=503,
        )
        self.assertNotContains(response, "database detail", status_code=503)
        self.assertEqual(PollingStrategyRepository().list(), ())

    def test_storage_failure_is_safe_and_does_not_render_internal_detail(self) -> None:
        self.client.force_login(self.staff)
        with patch(
            "qbet.web.polling_settings.PollingStrategyRepository.list",
            side_effect=PollingStrategyPersistenceError("database detail"),
        ):
            response = self.client.get("/admin-area/polling/")

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response,
            "Polling strategy configuration is temporarily unavailable",
            status_code=503,
        )
        self.assertNotContains(response, "database detail", status_code=503)
