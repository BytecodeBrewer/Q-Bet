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

    def test_public_home_explains_current_and_future_engine_flow(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Market data in. Strategy result out.")
        self.assertContains(response, "BonusEngine")
        self.assertContains(response, "SportsCapitalEngine")
        self.assertContains(response, "SportsExchangeEngine")
        self.assertContains(response, "TicketEngine")
        self.assertContains(response, "PredictionMarketEngine")
        self.assertContains(response, "CryptoYieldEngine")
        self.assertContains(response, "MLEdgeLayer")
        self.assertContains(response, "Risk")
        self.assertContains(response, "Liquidity")
        self.assertContains(response, "Approval")
        self.assertContains(response, "Simulation")
        self.assertContains(response, "Execution")
        self.assertContains(response, "Settlement")
        self.assertContains(response, "Reporting / Monitoring")
        self.assertContains(response, 'data-home-flow')
        self.assertContains(response, 'data-future-delay="3000"')
        self.assertContains(response, 'data-future-layer')
        self.assertContains(response, 'data-flow-motion-toggle')
        self.assertContains(response, 'aria-pressed="false"')
        self.assertContains(response, "Pause motion")
        self.assertContains(response, 'viewBox="0 0 960 640"')
        self.assertContains(response, 'id="flow-bonus-path"')
        self.assertContains(response, 'id="flow-sports-path"')
        self.assertContains(response, 'id="flow-shared-path"')
        self.assertContains(response, 'id="flow-simulation-path"')
        self.assertContains(response, 'id="flow-execution-path"')
        self.assertContains(response, 'data-flow-station="simulation"')
        self.assertContains(response, 'data-flow-station="approval"')
        self.assertContains(response, 'data-flow-station="execution"')
        self.assertNotContains(response, 'data-flow-station="simulation-execution"')
        self.assertContains(
            response,
            "work either enters Simulation directly or goes through Approval before controlled Execution",
        )
        self.assertContains(
            response,
            "both routes → Settlement → Reporting / Monitoring",
        )
        self.assertContains(
            response,
            "Planned paths keep component-specific preparation and risk rules before shared liquidity and dispatch stages.",
        )
        self.assertNotContains(response, 'd="M470 504 V210"')
        self.assertContains(response, "data-flow-packet", count=4)
        self.assertContains(response, "qbet_web/home.js")
        self.assertContains(response, "Sign in")
        self.assertContains(response, "Create account")
        self.assertContains(response, "phase2_visual.css")

        self.assertNotContains(response, "One protected path from data to decision.")
        self.assertNotContains(response, "Every proposal passes the same control envelope.")
        self.assertNotContains(response, "Kubernetes")
        self.assertNotContains(response, "Azure")
        self.assertNotContains(response, "provider-specific browser")

    def test_public_home_flow_script_moves_packets_and_reveals_future_after_three_seconds(
        self,
    ) -> None:
        script = Path(
            settings.BASE_DIR,
            "static",
            "qbet_web",
            "home.js",
        ).read_text(encoding="utf-8")

        self.assertIn("FUTURE_REVEAL_DELAY_MS", script)
        self.assertIn("Number(flow.dataset.futureDelay || 3000)", script)
        self.assertIn('flow.classList.add("has-motion")', script)
        self.assertIn('flow.classList.add("is-future-visible")', script)
        self.assertIn("getPointAtLength", script)
        self.assertIn("requestAnimationFrame(tick)", script)
        self.assertIn("pulseCrossedCheckpoints", script)
        self.assertIn('"IntersectionObserver" in window', script)
        self.assertIn('matchMedia("(prefers-reduced-motion: reduce)")', script)
        self.assertIn('matchMedia("(min-width: 781px)")', script)
        self.assertIn("!desktopFlow.matches", script)
        self.assertIn('desktopFlow.addEventListener("change"', script)
        self.assertIn('flow.querySelector("[data-flow-motion-toggle]")', script)
        self.assertIn("motionPaused", script)
        self.assertIn("setMotionPaused", script)
        self.assertIn("cancelFutureReveal", script)
        self.assertIn("if (!motionPaused)", script)
        self.assertIn('motionToggle.setAttribute("aria-pressed"', script)
        self.assertIn('"Resume motion" : "Pause motion"', script)
        self.assertIn("scheduleFutureReveal()", script)

    def test_public_home_styles_keep_future_muted_and_mobile_flow_static(self) -> None:
        visual_styles = Path(
            settings.BASE_DIR,
            "static",
            "qbet_web",
            "phase2_visual.css",
        ).read_text(encoding="utf-8")

        self.assertIn(".home-flow-shell.has-motion:not(.is-future-visible) .flow-future-layer", visual_styles)
        self.assertIn("stroke-dasharray: 5 8;", visual_styles)
        self.assertIn(".flow-future-engine rect", visual_styles)
        self.assertIn(".flow-packet-shared", visual_styles)
        self.assertIn(".flow-simulation-path", visual_styles)
        self.assertIn(".flow-execution-path", visual_styles)
        self.assertIn(".flow-execution-station circle", visual_styles)
        self.assertIn(".flow-toolbar", visual_styles)
        self.assertIn(".flow-motion-toggle", visual_styles)
        self.assertIn(".flow-engine-name", visual_styles)
        self.assertIn("font-size: 20px;", visual_styles)
        self.assertIn(".flow-station text", visual_styles)
        self.assertIn("font-size: 17px;", visual_styles)
        self.assertIn(".flow-future-engine text", visual_styles)
        self.assertIn("font-size: 18px;", visual_styles)
        self.assertIn("@media (max-width: 780px)", visual_styles)
        self.assertIn(".home-flow-map,", visual_styles)
        self.assertIn("display: none;", visual_styles)
        self.assertIn(".home-flow-mobile {", visual_styles)
        public_styles = visual_styles.split("/* Dashboard control strip */", maxsplit=1)[0]
        self.assertIn("@media (prefers-reduced-motion: reduce)", public_styles)
        reduced_motion = public_styles.split("@media (prefers-reduced-motion: reduce)")[-1]
        self.assertIn(".flow-motion-toggle { display: none; }", reduced_motion)
        self.assertIn(".flow-packet { display: none; }", reduced_motion)
        future_reduced_rule = reduced_motion.split(".flow-future-layer {", maxsplit=1)[1].split(
            "}", maxsplit=1
        )[0]
        self.assertIn("opacity: .76 !important;", future_reduced_rule)
        self.assertIn("transform: none !important;", future_reduced_rule)
        self.assertIn("transition: none !important;", future_reduced_rule)

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
        self.assertIn('placeholder.className = "dashboard-drag-placeholder";', dashboard_script)
        self.assertIn("dnd.closestInsertionSlot(", dashboard_script)
        self.assertIn("dnd.movePlaceholder(", dashboard_script)
        self.assertNotIn("layoutCompensation", dashboard_script)
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
