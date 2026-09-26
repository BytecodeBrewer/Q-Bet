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

    def test_public_home_explains_multi_engine_product_and_shared_protection(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Separate strategies. One protected path from data to decision.",
        )
        self.assertContains(response, "BonusEngine")
        self.assertContains(response, "SportsCapitalEngine")
        self.assertContains(response, "Future engines")
        self.assertContains(response, "Promotions + sports market data")
        self.assertContains(response, "Structured odds and market data")
        self.assertContains(response, "Domain-specific sources")
        self.assertContains(response, "Shared protected Q-Bet workflow")
        self.assertContains(response, "Domain Risk")
        self.assertContains(response, "Liquidity Check")
        self.assertContains(response, "User authority")
        self.assertContains(response, "Simulation or Execution")
        self.assertContains(response, "Settlement")
        self.assertContains(response, "Portfolio, Reporting & Monitoring")
        self.assertContains(response, "Current")
        self.assertContains(response, "Planned")
        self.assertContains(response, "Higher automation")
        self.assertContains(
            response,
            "removing human approval allows configured actions to execute without an individual confirmation step",
        )
        self.assertContains(response, "Sign in")
        self.assertContains(response, "Create account")
        self.assertContains(response, "phase2_visual.css")

        self.assertNotContains(response, "Kubernetes")
        self.assertNotContains(response, "Azure")
        self.assertNotContains(response, "browser extension")
        self.assertNotContains(response, "provider-specific browser")
        self.assertNotContains(response, "BaseEngine")
        self.assertNotContains(response, "YieldEngine")
        self.assertNotContains(response, "AlphaEngine")
        self.assertNotContains(response, "qbet_web/home.js")
        self.assertNotContains(response, "data-flow-packet")
        self.assertNotContains(response, "<svg")

    def test_public_home_styles_cover_narrow_layout_and_reduced_motion(self) -> None:
        visual_styles = Path(
            settings.BASE_DIR,
            "static",
            "qbet_web",
            "phase2_visual.css",
        ).read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 780px)", visual_styles)
        self.assertIn(".architecture-lanes,", visual_styles)
        self.assertIn(".protected-workflow-stages { grid-template-columns: 1fr; }", visual_styles)
        self.assertIn(".authority-grid { grid-template-columns: 1fr; }", visual_styles)
        self.assertIn("@media (max-width: 520px)", visual_styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", visual_styles)
        self.assertIn("animation: none !important;", visual_styles)
        self.assertIn("transition: none !important;", visual_styles)

    def test_authenticated_home_stays_presentation_first_and_links_to_dashboard(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open Dashboard")
        self.assertContains(response, "Current vs future")
        self.assertNotContains(response, "Active matches")
        self.assertNotContains(response, "Warnings / errors")

    def test_staff_dashboard_places_runtime_control_before_pointer_drag_handle(self) -> None:
        self.client.force_login(self.staff)

        response = self.client.get("/dashboard/")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        unavailable = 'title="Provider path unavailable"'
        drag_handle = 'aria-label="Move BonusEngine"'
        self.assertIn(unavailable, content)
        self.assertIn(drag_handle, content)
        self.assertLess(content.index(unavailable), content.index(drag_handle))
        self.assertNotIn('aria-label="Enable BonusEngine execution"', content)
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
