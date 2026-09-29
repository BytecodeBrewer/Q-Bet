import re
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


def _css_rule(styles: str, selector: str) -> str:
    pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
    match = re.search(pattern, styles, re.DOTALL)
    if match is None:
        raise AssertionError(f"missing CSS rule for {selector}")
    return match.group(1)


def _pixel_property(rule: str, property_name: str) -> int:
    match = re.search(rf"{re.escape(property_name)}:\s*(\d+)px", rule)
    if match is None:
        raise AssertionError(f"missing pixel property {property_name}")
    return int(match.group(1))


def _keyframe_section(styles: str, name: str) -> str:
    start = styles.index(f"@keyframes {name}")
    next_keyframe = styles.find("@keyframes ", start + 1)
    media_end = styles.find("\n}\n\n.authority-grid", start)
    candidates = [index for index in (next_keyframe, media_end) if index != -1]
    end = min(candidates) if candidates else len(styles)
    return styles[start:end]


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

    def test_public_home_renders_sequential_workflow_motion_hooks(self) -> None:
        response = self.client.get("/")
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-workflow-motion="sequential"', content)
        self.assertIn('data-workflow-lane="bonus"', content)
        self.assertIn('data-workflow-lane="sports-capital"', content)
        self.assertIn('data-workflow-lane="future"', content)
        self.assertEqual(content.count('data-workflow-step="data"'), 3)
        self.assertEqual(content.count('data-workflow-step="preparation"'), 3)
        self.assertEqual(content.count('data-workflow-step="calculation"'), 3)
        self.assertIn('data-workflow-stage="risk"', content)
        self.assertIn('data-workflow-stage="liquidity"', content)
        self.assertIn('data-workflow-stage="authority"', content)
        self.assertIn('data-workflow-stage="simulation-execution"', content)
        self.assertIn('data-workflow-stage="settlement"', content)
        self.assertIn('data-workflow-stage="reporting"', content)
        self.assertIn('<span class="capability-badge is-planned">Planned</span>', content)
        self.assertNotIn("qbet_web/home.js", content)
        self.assertNotIn("<svg", content)

    def test_public_home_styles_cover_narrow_layout_and_reduced_motion(self) -> None:
        visual_styles = Path(
            settings.BASE_DIR,
            "static",
            "qbet_web",
            "phase2_visual.css",
        ).read_text(encoding="utf-8")

        self.assertIn(
            "@media (prefers-reduced-motion: no-preference) and (min-width: 781px)",
            visual_styles,
        )
        self.assertIn(
            '.architecture-frame[data-workflow-motion="sequential"]',
            visual_styles,
        )
        self.assertIn("@keyframes workflow-step-pulse", visual_styles)
        self.assertIn("@keyframes workflow-merge-pulse", visual_styles)
        self.assertIn("@keyframes workflow-stage-focus", visual_styles)
        self.assertIn("@media (max-width: 780px)", visual_styles)
        self.assertIn(".architecture-lanes,", visual_styles)
        self.assertIn(".protected-workflow-stages { grid-template-columns: 1fr; }", visual_styles)
        self.assertIn(".authority-grid { grid-template-columns: 1fr; }", visual_styles)
        self.assertIn("@media (max-width: 520px)", visual_styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", visual_styles)
        self.assertIn("content: none !important;", visual_styles)
        self.assertIn("animation: none !important;", visual_styles)
        self.assertIn("transition: none !important;", visual_styles)

    def test_public_home_workflow_motion_geometry_has_safe_bounds(self) -> None:
        visual_styles = Path(
            settings.BASE_DIR,
            "static",
            "qbet_web",
            "phase2_visual.css",
        ).read_text(encoding="utf-8")

        step_gap = _pixel_property(_css_rule(visual_styles, ".engine-lane-flow"), "gap")
        stage_gap = _pixel_property(
            _css_rule(visual_styles, ".protected-workflow-stages"),
            "gap",
        )
        step_height = _pixel_property(
            _css_rule(visual_styles, ".engine-lane-flow li"),
            "min-height",
        )
        merge_height = _pixel_property(
            _css_rule(visual_styles, ".architecture-merge"),
            "height",
        )
        merge_marker = _css_rule(
            visual_styles,
            '.architecture-frame[data-workflow-motion="sequential"] .architecture-merge::before',
        )
        merge_top = _pixel_property(merge_marker, "top")
        merge_size = _pixel_property(merge_marker, "width")
        step_marker = _css_rule(
            visual_styles,
            '.architecture-frame[data-workflow-motion="sequential"] .engine-lane-flow li[data-workflow-step]::before',
        )
        step_marker_left = _pixel_property(step_marker, "left")
        step_marker_size = _pixel_property(step_marker, "width")

        card_shifts = []
        for keyframe in (
            "workflow-lane-focus",
            "workflow-step-focus",
            "workflow-stage-focus",
        ):
            card_shifts.extend(
                int(value)
                for value in re.findall(
                    r"translateY\((-?\d+)px\)",
                    _keyframe_section(visual_styles, keyframe),
                )
            )
        max_card_shift = max(abs(value) for value in card_shifts)

        merge_keyframes = _keyframe_section(visual_styles, "workflow-merge-pulse")
        merge_travel = [
            int(value)
            for value in re.findall(
                r"translateY\((-?\d+)px\)",
                merge_keyframes,
            )
        ]
        merge_shadow = max(
            int(value)
            for value in re.findall(
                r"box-shadow:\s*0 0 0 (\d+)px",
                merge_marker,
            )
        )
        merge_radius = merge_size / 2

        step_keyframes = _keyframe_section(visual_styles, "workflow-step-pulse")
        step_shadow = max(
            int(value)
            for value in re.findall(
                r"box-shadow:\s*0 0 0 (\d+)px",
                step_keyframes,
            )
        )
        step_radius = step_marker_size / 2

        # Animated cards move by at most two pixels while normal-flow gaps stay
        # substantially larger, so adjacent rows cannot collide during motion.
        self.assertGreater(step_gap, max_card_shift * 2)
        self.assertGreater(stage_gap, max_card_shift * 2)

        # The merge pulse remains inside its dedicated 58px connector strip,
        # including the visual halo at both animation extremes.
        self.assertGreaterEqual(
            merge_top + min(merge_travel) - merge_radius - merge_shadow,
            0,
        )
        self.assertLessEqual(
            merge_top + max(merge_travel) + merge_radius + merge_shadow,
            merge_height,
        )

        # Step pulses and their halo remain inside the step-card box.
        self.assertGreaterEqual(step_marker_left - step_radius - step_shadow, 0)
        self.assertGreaterEqual(
            step_height / 2 - step_radius - step_shadow,
            0,
        )
        self.assertLessEqual(
            step_height / 2 + step_radius + step_shadow,
            step_height,
        )

        # Responsive geometry is deterministic at the animation boundary:
        # normal-motion desktop keeps three lanes, while 780px and below
        # collapses to one static lane and removes the merge connector.
        self.assertIn(
            "@media (prefers-reduced-motion: no-preference) and (min-width: 781px)",
            visual_styles,
        )
        narrow_start = visual_styles.index("@media (max-width: 780px)")
        narrow_end = visual_styles.index("@media (max-width: 520px)", narrow_start)
        narrow_styles = visual_styles[narrow_start:narrow_end]
        self.assertIn(".architecture-lanes,", narrow_styles)
        self.assertIn("grid-template-columns: 1fr;", narrow_styles)
        self.assertIn(".architecture-merge {\n    display: none;", narrow_styles)

        reduced_start = visual_styles.index("@media (prefers-reduced-motion: reduce)")
        reduced_end = visual_styles.index("/* Dashboard control strip */", reduced_start)
        reduced_styles = visual_styles[reduced_start:reduced_end]
        self.assertIn("animation: none !important;", reduced_styles)
        self.assertIn("content: none !important;", reduced_styles)
        self.assertIn("transform: none !important;", reduced_styles)

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
