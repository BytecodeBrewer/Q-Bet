from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.storage.ledger import (
    RoutingConfigurationPersistenceError,
    RoutingConfigurationRepository,
)
from qbet.storage.models import ExecutionRecordRow, ModeWorkQueueRow, PortfolioLedgerRow
from qbet.workflow.routing import EngineModes, RoutingConfiguration


class RoutingSettingsTests(TestCase):
    def setUp(self) -> None:
        self.staff = User.objects.create_user(
            "routing-staff",
            password="Strong-pass-123",
            is_staff=True,
        )
        self.user = User.objects.create_user(
            "routing-user",
            password="Strong-pass-123",
        )

    def test_routing_settings_are_staff_only_and_default_inactive(self) -> None:
        anonymous = self.client.get("/admin-area/gui-settings/")
        self.assertEqual(anonymous.status_code, 302)

        self.client.force_login(self.user)
        normal_user = self.client.get("/admin-area/gui-settings/")
        normal_user_write = self.client.post(
            "/admin-area/gui-settings/",
            {"bonus": "both", "sports_capital": "both"},
        )
        self.assertEqual(normal_user.status_code, 302)
        self.assertEqual(normal_user_write.status_code, 302)
        self.assertIsNone(RoutingConfigurationRepository().load())

        self.client.force_login(self.staff)
        response = self.client.get("/admin-area/gui-settings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BonusEngine")
        self.assertContains(response, "SportsCapitalEngine")
        self.assertContains(response, "Inactive", count=4)
        self.assertContains(response, "safe inactive defaults are active")

    def test_staff_save_rejects_bonus_activation_until_provider_path_exists(self) -> None:
        self.client.force_login(self.staff)

        response = self.client.post(
            "/admin-area/gui-settings/",
            {"bonus": "both", "sports_capital": "simulation"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "BonusEngine provider path is not connected yet.",
            status_code=400,
        )
        self.assertIsNone(RoutingConfigurationRepository().load())
        self.assertEqual(ModeWorkQueueRow.objects.count(), 0)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)

    def test_staff_can_persist_sports_capital_without_bonus_activation(self) -> None:
        self.client.force_login(self.staff)

        response = self.client.post(
            "/admin-area/gui-settings/",
            {"bonus": "inactive", "sports_capital": "simulation"},
        )

        self.assertRedirects(response, "/admin-area/gui-settings/")
        self.assertEqual(
            RoutingConfigurationRepository().load(),
            RoutingConfiguration(
                bonus=EngineModes(),
                sports_capital=EngineModes(simulation=True),
            ),
        )
        self.assertEqual(ModeWorkQueueRow.objects.count(), 0)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)


    def test_invalid_mode_or_unknown_engine_does_not_change_saved_configuration(self) -> None:
        repository = RoutingConfigurationRepository()
        original = repository.save(
            RoutingConfiguration(bonus=EngineModes(simulation=True))
        )
        self.client.force_login(self.staff)

        invalid_mode = self.client.post(
            "/admin-area/gui-settings/",
            {"bonus": "live", "sports_capital": "inactive"},
        )
        self.assertEqual(invalid_mode.status_code, 400)
        self.assertEqual(repository.load(), original)

        unknown_engine = self.client.post(
            "/admin-area/gui-settings/",
            {
                "bonus": "simulation",
                "sports_capital": "inactive",
                "ticket": "both",
            },
        )
        self.assertEqual(unknown_engine.status_code, 400)
        self.assertEqual(repository.load(), original)

    def test_unavailable_routing_store_has_safe_error_state(self) -> None:
        self.client.force_login(self.staff)
        with patch(
            "qbet.web.routing_settings.RoutingConfigurationRepository.load",
            side_effect=RoutingConfigurationPersistenceError("database detail"),
        ):
            response = self.client.get("/admin-area/gui-settings/")

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response,
            "Routing configuration is temporarily unavailable",
            status_code=503,
        )
        self.assertNotContains(response, "database detail", status_code=503)

    def test_save_failure_preserves_previous_configuration_and_hides_internal_detail(self) -> None:
        repository = RoutingConfigurationRepository()
        original = repository.save(
            RoutingConfiguration(sports_capital=EngineModes(execution=True))
        )
        self.client.force_login(self.staff)

        with patch(
            "qbet.web.routing_settings.RoutingConfigurationRepository.save",
            side_effect=RoutingConfigurationPersistenceError("database detail"),
        ):
            response = self.client.post(
                "/admin-area/gui-settings/",
                {"bonus": "inactive", "sports_capital": "both"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response,
            "routing configuration could not be saved",
            status_code=503,
        )
        self.assertNotContains(response, "database detail", status_code=503)
        self.assertEqual(repository.load(), original)
