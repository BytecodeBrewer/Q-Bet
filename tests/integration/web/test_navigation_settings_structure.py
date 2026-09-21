from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.web.models import SimulationAvailability


class NavigationAndSettingsStructureTests(TestCase):
    def test_authenticated_shell_separates_sidebar_from_work_actions(self) -> None:
        user = User.objects.create_user("nav-user", password="Valid-pass-12345")
        self.client.force_login(user)
        with (
            patch("qbet.web.approval_context._APPROVALS.active_count_for", return_value=2),
            patch(
                "qbet.web.approval_context._NOTIFICATION_INBOX.list",
                return_value=(SimpleNamespace(read=False), SimpleNamespace(read=True)),
            ),
        ):
            response = self.client.get("/dashboard/")

        content = response.content.decode()
        self.assertContains(response, 'id="app-sidebar"')
        self.assertContains(response, "data-sidebar-toggle")
        self.assertContains(response, "Main Dashboard")
        self.assertContains(response, "Execution Engine")
        self.assertContains(response, "Notifications")
        self.assertContains(response, "Approvals")
        self.assertContains(response, 'class="nav-count">1</span>')
        self.assertContains(response, 'class="nav-count">2</span>')
        sidebar = content.split('id="app-sidebar"', 1)[1].split("</aside>", 1)[0]
        self.assertNotIn("Notifications", sidebar)
        self.assertNotIn("Approvals", sidebar)
        self.assertNotIn("Simulation Dashboard", sidebar)
        self.assertNotIn("/monitoring/", sidebar)

    def test_staff_simulation_navigation_tracks_global_availability(self) -> None:
        staff = User.objects.create_user(
            "staff-nav",
            password="Valid-pass-12345",
            is_staff=True,
        )
        self.client.force_login(staff)

        disabled = self.client.get("/dashboard/")
        self.assertContains(disabled, "Simulation unavailable")
        self.assertNotContains(disabled, "BonusEngine Simulation")

        SimulationAvailability.objects.update_or_create(id=1, defaults={"enabled": True})
        enabled = self.client.get("/dashboard/")
        self.assertContains(enabled, "Simulation Dashboard")
        self.assertContains(enabled, "BonusEngine Simulation")
        self.assertContains(enabled, "SportsCapitalEngine Simulation")
        self.assertContains(enabled, "#simulation-bonus")
        self.assertContains(enabled, "#simulation-sports_capital")

    def test_settings_toc_separates_user_and_staff_sections(self) -> None:
        user = User.objects.create_user(
            "settings-user",
            email="settings@example.com",
            password="Valid-pass-12345",
        )
        self.client.force_login(user)

        response = self.client.get("/settings/presentation/")

        self.assertContains(response, 'class="settings-toc"')
        self.assertContains(response, "Profile and account data")
        self.assertContains(response, "Email and verification")
        self.assertContains(response, "Password and security")
        self.assertContains(response, "Notification settings")
        self.assertContains(response, "Engine and mode preferences")
        self.assertContains(response, "Appearance")
        self.assertNotContains(response, "Global engine availability")
        self.assertNotContains(response, "Smart Polling")
        self.assertNotContains(response, "/monitoring/")

        staff = User.objects.create_user(
            "settings-staff",
            email="staff@example.com",
            password="Valid-pass-12345",
            is_staff=True,
        )
        self.client.force_login(staff)
        staff_response = self.client.get("/settings/presentation/")
        self.assertContains(staff_response, "Global engine availability")
        self.assertContains(staff_response, "Smart Polling")
        self.assertContains(staff_response, "/monitoring/")
        self.assertContains(staff_response, "/admin/")

    def test_normal_user_cannot_open_staff_control_surfaces(self) -> None:
        user = User.objects.create_user("boundary-user", password="Valid-pass-12345")
        self.client.force_login(user)

        self.assertEqual(self.client.get("/simulation/").status_code, 404)
        self.assertEqual(self.client.get("/monitoring/").status_code, 302)
        self.assertEqual(self.client.get("/admin-area/").status_code, 302)
        self.assertEqual(self.client.get("/admin-area/gui-settings/").status_code, 302)
        self.assertEqual(self.client.get("/admin-area/polling/").status_code, 302)

    def test_sidebar_toggle_contract_is_rendered_for_authenticated_layout(self) -> None:
        user = User.objects.create_user("toggle-user", password="Valid-pass-12345")
        self.client.force_login(user)

        response = self.client.get("/dashboard/")

        self.assertContains(response, 'aria-controls="app-sidebar"')
        self.assertContains(response, 'aria-expanded="true"')
        self.assertContains(response, "/static/qbet_web/navigation.js")
