from pathlib import Path
from unittest.mock import patch

from django.conf import settings
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
        self.assertContains(response, "Q-Bet opportunity pipeline")
        self.assertContains(response, "Data Providers")
        self.assertContains(response, "Odds APIs")
        self.assertContains(response, "Result APIs")
        self.assertContains(response, "Market APIs")
        self.assertContains(response, 'data-flow-station="aggregation"')
        self.assertContains(response, 'data-flow-station="builder"')
        self.assertContains(response, 'data-flow-station="engine"')
        self.assertContains(response, 'data-flow-station="risk"')
        self.assertContains(response, 'data-flow-station="liquidity"')
        self.assertContains(response, 'data-flow-station="ledger"')
        self.assertContains(response, 'data-flow-station="bank"')
        self.assertContains(response, 'data-flow-station="execution"')
        self.assertContains(response, 'id="flow-main-path"')
        self.assertContains(response, 'd="M56 210 H190 H330 H470 H610 H760 H930"')
        self.assertContains(response, 'id="flow-capital-path"')
        self.assertContains(response, "data-flow-packet", count=7)
        self.assertContains(
            response,
            'data-flow-pulse="aggregation:.15,builder:.31,engine:.47,risk:.63,liquidity:.81,execution:1"',
        )
        self.assertContains(response, 'data-flow-pulse="bank:0,ledger:.42,liquidity:1"')
        self.assertNotContains(response, "<animateMotion")
        self.assertNotContains(response, "Simulation")
        self.assertContains(response, "qbet_web/home.js")
        self.assertContains(response, "Sign in")
        self.assertContains(response, "Create account")
        self.assertContains(response, "phase2_visual.css")
        self.assertNotContains(response, "Active matches")
        self.assertNotContains(response, "Pending matches")
        self.assertNotContains(response, "Warnings / errors")
        self.assertNotContains(response, "Current state")
        self.assertNotContains(response, "Operator shortcuts")

        home_script = Path(settings.BASE_DIR, "static", "qbet_web", "home.js").read_text(
            encoding="utf-8"
        )
        visual_styles = Path(
            settings.BASE_DIR,
            "static",
            "qbet_web",
            "phase2_visual.css",
        ).read_text(encoding="utf-8")
        self.assertIn("getPointAtLength", home_script)
        self.assertIn("requestAnimationFrame(tick)", home_script)
        self.assertIn("pulseCrossedCheckpoints", home_script)
        self.assertIn('"IntersectionObserver" in window', home_script)
        self.assertIn("reducedMotion.addEventListener", home_script)
        self.assertIn("is-packet-hit", home_script)
        self.assertIn(".flow-provider-connector", visual_styles)
        self.assertIn("stroke-dasharray: 5 8;", visual_styles)
        self.assertIn(".flow-capital-connector", visual_styles)
        self.assertIn(".flow-packet-capital", visual_styles)
        self.assertIn(".flow-station.is-packet-hit circle", visual_styles)
        self.assertNotIn("station-breathe", visual_styles)

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
        runtime_control = 'aria-label="Enable BonusEngine execution"'
        drag_handle = 'aria-label="Move BonusEngine"'
        self.assertIn(runtime_control, content)
        self.assertIn(drag_handle, content)
        self.assertLess(content.index(runtime_control), content.index(drag_handle))
        self.assertNotIn('draggable="true"', content)
        self.assertIn("data-drag-handle", content)

        dashboard_script = Path(
            settings.BASE_DIR,
            "static",
            "qbet_web",
            "dashboard.js",
        ).read_text(encoding="utf-8")
        self.assertIn("const DRAG_START_DISTANCE = 10;", dashboard_script)
        self.assertIn("const REORDER_HYSTERESIS = 10;", dashboard_script)
        self.assertIn('dragAxis = isSingleColumn(grid) ? "y" : "free";', dashboard_script)
        self.assertIn('draggedCard.style.transition = "none";', dashboard_script)
        self.assertIn(
            "layoutCompensationY += draggedBefore.top - draggedAfter.top;",
            dashboard_script,
        )
        self.assertIn("function isSingleColumn(grid)", dashboard_script)

    def test_root_canvas_uses_selected_theme_for_mobile_viewport(self) -> None:
        base_template = Path(
            settings.BASE_DIR,
            "templates",
            "qbet_web",
            "base.html",
        ).read_text(encoding="utf-8")

        self.assertIn(
            '<html lang="{{ display_preferences.language|default:\'de\' }}" data-theme="{{ preferences.theme|default:\'light\' }}">',
            base_template,
        )
        self.assertIn(
            'html[data-theme="dark"] { background: #181c19; color-scheme: dark; }',
            base_template,
        )
        self.assertIn("body { min-height: 100dvh; }", base_template)

    def test_staff_monitoring_degrades_to_explicit_unavailable_state(self) -> None:
        self.client.force_login(self.staff)
        unavailable = MonitoringService(_UnavailableReader())

        with patch("qbet.web.views.WORKFLOW_MONITORING_SERVICE", unavailable):
            response = self.client.get("/monitoring/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monitoring history unavailable")
        self.assertContains(response, "Workflow history cannot be read")
        self.assertContains(response, "Readiness")
