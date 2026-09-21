import re

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.storage.ledger import (
    RoutingConfigurationRepository,
    UserRoutingPreferenceRepository,
)
from qbet.storage.models import ExecutionRecordRow, ModeWorkQueueRow, PortfolioLedgerRow
from qbet.workflow.routing import (
    EngineModes,
    RoutingConfiguration,
    UserEngineModes,
    UserRoutingPreferences,
)


class UserRoutingPreferenceSettingsTests(TestCase):
    def setUp(self) -> None:
        self.alice = User.objects.create_user("alice", password="Strong-pass-123")
        self.bob = User.objects.create_user("bob", password="Strong-pass-123")
        self.routing = RoutingConfigurationRepository()
        self.preferences = UserRoutingPreferenceRepository()
        self.routing.save(
            RoutingConfiguration(
                bonus=EngineModes(simulation=True, execution=True),
                sports_capital=EngineModes(simulation=True, execution=False),
            )
        )

    def test_authenticated_user_saves_only_own_globally_available_routes(self) -> None:
        self.client.force_login(self.alice)

        response = self.client.post(
            "/settings/engines/",
            {
                "bonus_simulation": "on",
                "bonus_execution": "on",
                "sports_capital_execution": "on",
            },
        )

        self.assertRedirects(response, "/settings/presentation/")
        alice = self.preferences.load("alice")
        bob = self.preferences.load("bob")
        self.assertTrue(alice.bonus.simulation)
        self.assertTrue(alice.bonus.execution)
        self.assertFalse(alice.sports_capital.execution)
        self.assertEqual(bob, UserRoutingPreferences())
        self.assertEqual(ModeWorkQueueRow.objects.count(), 0)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)

    def test_settings_read_only_current_users_preferences(self) -> None:
        self.preferences.save(
            "bob",
            UserRoutingPreferences(
                sports_capital=UserEngineModes(simulation=True)
            ),
        )
        self.client.force_login(self.alice)

        response = self.client.get("/settings/presentation/")

        match = re.search(
            r'<input[^>]+name="sports_capital_simulation"[^>]*>',
            response.content.decode(),
        )
        self.assertIsNotNone(match)
        self.assertNotIn("checked", match.group(0))

    def test_global_disable_retains_selected_preference_and_marks_it_unavailable(self) -> None:
        self.preferences.save(
            "alice",
            UserRoutingPreferences(
                bonus=UserEngineModes(simulation=True, execution=True)
            ),
        )
        self.routing.save(RoutingConfiguration(bonus=EngineModes(simulation=True)))
        self.client.force_login(self.alice)

        response = self.client.get("/settings/presentation/")

        match = re.search(
            r'<input[^>]+name="bonus_execution"[^>]*>',
            response.content.decode(),
        )
        self.assertIsNotNone(match)
        self.assertIn("disabled", match.group(0))
        self.assertIn("checked", match.group(0))
        self.assertContains(
            response,
            "Selected but unavailable while staff has disabled this route",
        )

        updated = self.client.post(
            "/settings/engines/",
            {"bonus_simulation": "on"},
        )
        self.assertRedirects(updated, "/settings/presentation/")
        self.assertTrue(self.preferences.load("alice").bonus.execution)

    def test_forged_user_identifier_cannot_modify_another_users_preferences(self) -> None:
        self.preferences.save(
            "bob",
            UserRoutingPreferences(bonus=UserEngineModes(simulation=True)),
        )
        self.client.force_login(self.alice)

        response = self.client.post(
            "/settings/engines/",
            {"bonus_simulation": "on", "user_id": "bob"},
        )

        self.assertRedirects(response, "/settings/presentation/")
        self.assertEqual(self.preferences.load("alice"), UserRoutingPreferences())
        self.assertTrue(self.preferences.load("bob").bonus.simulation)

    def test_engine_preferences_require_authentication(self) -> None:
        response = self.client.post("/settings/engines/", {"bonus_simulation": "on"})

        self.assertRedirects(
            response,
            "/accounts/login/?next=/settings/engines/",
        )
