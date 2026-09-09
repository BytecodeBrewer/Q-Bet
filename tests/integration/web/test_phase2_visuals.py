from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.monitoring import MonitoringQuery, MonitoringRecord, MonitoringService


class _UnavailableReader:
    def list_records(self, query: MonitoringQuery) -> tuple[MonitoringRecord, ...]:
        del query
        raise OSError("monitoring history is unavailable")


class Phase2VisualIntegrationTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("visual-user", password="Strong-pass-123")
        self.staff = User.objects.create_user(
            "visual-staff",
            password="Strong-pass-123",
            is_staff=True,
        )

    def test_public_home_is_presentation_first_and_contains_animated_flow(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turn market opportunities into controlled decisions.")
        self.assertContains(response, "Q-Bet opportunity flow")
        self.assertContains(response, "<animateMotion", count=3)
        self.assertContains(response, "Sign in")
        self.assertContains(response, "Create account")
        self.assertContains(response, "phase2_visual.css")
        self.assertNotContains(response, "Active matches")
        self.assertNotContains(response, "Pending matches")
        self.assertNotContains(response, "Warnings / errors")
        self.assertNotContains(response, "Current state")
        self.assertNotContains(response, "Operator shortcuts")

    def test_authenticated_home_stays_presentation_first_and_links_to_dashboard(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open Dashboard")
        self.assertNotContains(response, "Active matches")
        self.assertNotContains(response, "Warnings / errors")

    def test_staff_dashboard_places_runtime_control_before_pointer_drag_handle(self) -> None:
        self.client.force_login(self.staff)

        response = self.client.get("/dashboard/")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        runtime_control = 'aria-label="Start BonusEngine execution"'
        drag_handle = 'aria-label="Move BonusEngine"'
        self.assertIn(runtime_control, content)
        self.assertIn(drag_handle, content)
        self.assertLess(content.index(runtime_control), content.index(drag_handle))
        self.assertNotIn('draggable="true"', content)
        self.assertIn("data-drag-handle", content)

    def test_staff_monitoring_degrades_to_explicit_unavailable_state(self) -> None:
        self.client.force_login(self.staff)
        unavailable = MonitoringService(_UnavailableReader())

        with patch("qbet.web.views.WORKFLOW_MONITORING_SERVICE", unavailable):
            response = self.client.get("/monitoring/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monitoring history unavailable")
        self.assertContains(response, "Workflow history cannot be read")
        self.assertContains(response, "Readiness")
