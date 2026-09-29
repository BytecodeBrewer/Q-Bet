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

    @staticmethod
    def result_payload(**changes: str) -> dict[str, str]:
        payload = PollingSettingsTests.market_payload(
            target="result",
            market_refresh_points_minutes="",
            market_interval_minutes="",
            result_retry_minutes="12",
        )
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

    def test_overview_shows_default_override_and_missing_effective_route(self) -> None:
        self.client.force_login(self.staff)
        self.client.post(
            "/admin-area/polling/",
            self.market_payload(engine="", freshness_minutes="8"),
        )
        self.client.post(
            "/admin-area/polling/",
            self.market_payload(
                engine="bonus",
                freshness_minutes="2",
                market_refresh_points_minutes="120,15",
            ),
        )

        response = self.client.get("/admin-area/polling/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Provider/target Default")
        self.assertContains(response, "BonusEngine")
        self.assertContains(response, "SportsCapitalEngine")
        self.assertContains(response, "Override")
        self.assertContains(response, "replaces the Default")
        self.assertContains(response, "Uses the provider/target Default")
        self.assertContains(response, "Freshness 2 min")
        self.assertContains(response, "Freshness 8 min")
        self.assertNotContains(response, "api_key")

        self.assertContains(response, "Effective strategy")
        self.assertContains(response, "Override")
        self.assertContains(response, "Default")

    def test_override_without_default_makes_other_engine_visibly_unavailable(self) -> None:
        self.client.force_login(self.staff)
        self.client.post(
            "/admin-area/polling/",
            self.market_payload(engine="bonus"),
        )

        response = self.client.get("/admin-area/polling/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No provider/target Default is configured")
        self.assertContains(response, "BonusEngine")
        self.assertContains(response, "SportsCapitalEngine")
        self.assertContains(response, "No strategy configured")

    def test_market_and_result_targets_are_grouped_separately(self) -> None:
        self.client.force_login(self.staff)
        self.client.post("/admin-area/polling/", self.market_payload(engine=""))
        self.client.post("/admin-area/polling/", self.result_payload(engine=""))

        response = self.client.get("/admin-area/polling/")

        self.assertContains(response, "Market target")
        self.assertContains(response, "Result target")
        self.assertContains(response, "Retry every 12 min")

    def test_edit_mode_preloads_current_values_and_cancel_does_not_mutate(self) -> None:
        self.client.force_login(self.staff)
        self.client.post(
            "/admin-area/polling/",
            self.market_payload(
                engine="bonus",
                freshness_minutes="7",
                capacity_units="42",
            ),
        )

        response = self.client.get(
            "/admin-area/polling/"
            "?edit_provider=provider-a&edit_source=odds&edit_target=market&edit_engine=bonus"
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('name="freshness_minutes" value="7"', html)
        self.assertIn('name="capacity_units" value="42"', html)
        for field_name in ("provider_id", "source_id", "transport", "target", "engine"):
            self.assertRegex(
                html,
                rf'name="{field_name}"[^>]*disabled',
            )
        self.assertContains(response, "Cancel without saving")
        self.assertNotContains(response, "Save reviewed Smart Polling strategy")
        self.assertContains(
            response,
            "Preview the effective engine routes before Save becomes available.",
        )

        unchanged = PollingStrategyRepository().load(
            provider_id="provider-a",
            source_id="odds",
            target=PollingTarget.MARKET,
            engine="bonus",
        )
        assert unchanged is not None
        self.assertEqual(int(unchanged.freshness_window.total_seconds() // 60), 7)

    def test_preset_application_exposes_concrete_values_without_saving(self) -> None:
        self.client.force_login(self.staff)

        payload = self.market_payload()
        payload.update({"action": "apply_preset", "preset": "conservative"})
        response = self.client.post("/admin-area/polling/", payload)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('name="freshness_minutes" value="15"', html)
        self.assertIn(
            'name="market_refresh_points_minutes" value="1440,120,15"',
            html,
        )
        self.assertIn(
            'name="latest_market_poll_before_event_minutes" value="5"',
            html,
        )
        self.assertIn('name="result_retry_minutes" value="20"', html)
        self.assertIn('name="max_attempts" value="2"', html)
        self.assertIn('name="capacity_units" value="100"', html)
        self.assertEqual(PollingStrategyRepository().list(), ())
        self.assertContains(response, "Presets change timing and attempt values only")

    def test_preview_uses_effective_resolution_and_does_not_mutate(self) -> None:
        self.client.force_login(self.staff)
        self.client.post(
            "/admin-area/polling/",
            self.market_payload(engine="", freshness_minutes="5"),
        )
        self.client.post(
            "/admin-area/polling/",
            self.market_payload(
                engine="bonus",
                freshness_minutes="2",
                market_refresh_points_minutes="120,15",
            ),
        )

        preview_payload = self.market_payload(
            engine="",
            freshness_minutes="9",
        )
        preview_payload["action"] = "preview"
        response = self.client.post("/admin-area/polling/", preview_payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview only · not saved")
        self.assertContains(response, "SportsCapitalEngine · provider-a/odds · Market")
        self.assertContains(response, "freshness 5 min")
        self.assertContains(response, "freshness 9 min")
        self.assertContains(response, "Save reviewed Smart Polling strategy")

        persisted = PollingStrategyRepository().load(
            provider_id="provider-a",
            source_id="odds",
            target=PollingTarget.MARKET,
            engine=None,
        )
        assert persisted is not None
        self.assertEqual(int(persisted.freshness_window.total_seconds() // 60), 5)

    def test_edit_save_updates_same_strategy_identity(self) -> None:
        self.client.force_login(self.staff)
        self.client.post(
            "/admin-area/polling/",
            self.market_payload(engine="bonus", freshness_minutes="5"),
        )

        payload = self.market_payload(
            provider_id="ignored-provider",
            source_id="ignored-source",
            target="result",
            engine="sports_capital",
            freshness_minutes="11",
        )
        payload.update(
            {
                "action": "save",
                "edit_provider": "provider-a",
                "edit_source": "odds",
                "edit_target": "market",
                "edit_engine": "bonus",
            }
        )
        for field in ("provider_id", "source_id", "transport", "target", "engine"):
            payload.pop(field, None)

        response = self.client.post("/admin-area/polling/", payload)

        self.assertRedirects(response, "/admin-area/polling/")
        repository = PollingStrategyRepository()
        updated = repository.load(
            provider_id="provider-a",
            source_id="odds",
            target=PollingTarget.MARKET,
            engine="bonus",
        )
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(int(updated.freshness_window.total_seconds() // 60), 11)
        self.assertEqual(len(repository.list()), 1)

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

    def test_invalid_or_unsupported_configuration_is_rejected_next_to_fields(self) -> None:
        self.client.force_login(self.staff)

        unordered = self.client.post(
            "/admin-area/polling/",
            self.market_payload(market_refresh_points_minutes="60,720"),
        )
        self.assertEqual(unordered.status_code, 400)
        self.assertContains(
            unordered,
            "Refresh points must be positive, unique, and ordered far-to-near",
            status_code=400,
        )
        self.assertEqual(PollingStrategyRepository().list(), ())

        contradictory = self.client.post(
            "/admin-area/polling/",
            self.market_payload(market_interval_minutes="5"),
        )
        self.assertEqual(contradictory.status_code, 400)
        self.assertContains(
            contradictory,
            "Choose explicit refresh points or a fallback interval, not both.",
            count=2,
            status_code=400,
        )
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
