from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.layers import SimulationLogRecord
from qbet.reporting import SimulationReport
from qbet.simulation import SimulationEngine, SimulationRunConfig, SimulationStatus
from qbet.web.models import SimulationAvailability
from qbet.web.monitoring import MonitoringService


class _ReportStore:
    def __init__(self, report: SimulationReport) -> None:
        self.report = report

    def list_recent_reports(self, limit: int = 20) -> tuple[SimulationReport, ...]:
        return (self.report,)[:limit]

    def load_report(self, run_id: UUID) -> SimulationReport:
        if run_id != self.report.run_id:
            raise KeyError(run_id)
        return self.report

    def load_records(self, run_id: UUID) -> tuple[SimulationLogRecord, ...]:
        raise KeyError(run_id)


class DashboardInteractionTests(TestCase):
    def setUp(self) -> None:
        SimulationAvailability.objects.all().delete()
        self.staff = User.objects.create_user(
            "staff-dashboard-78",
            password="Strong-pass-123",
            is_staff=True,
        )

    def test_staff_simulation_cards_expose_progress_and_keyboard_reorder_controls(self) -> None:
        report = SimulationReport(
            run_id=uuid4(),
            config=SimulationRunConfig(
                engine=SimulationEngine.BONUS,
                starting_capital=Decimal("100"),
            ),
            engine=SimulationEngine.BONUS.value,
            strategy_id=None,
            status=SimulationStatus.RUNNING,
            starting_capital=Decimal("100"),
            current_capital=Decimal("101"),
            top_up_total=Decimal("0"),
            profit_loss=Decimal("1"),
            completed_steps=(),
            elapsed_duration=timedelta(minutes=1),
            progress=Decimal("0.50"),
            generated_at=datetime(2026, 9, 4, tzinfo=UTC),
        )
        service = MonitoringService(_ReportStore(report))
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.staff)

        with patch("qbet.web.views.MONITORING_SERVICE", service):
            response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Progress")
        self.assertContains(response, "0.50")
        self.assertContains(
            response,
            'aria-keyshortcuts="ArrowLeft ArrowRight ArrowUp ArrowDown"',
            count=4,
        )
        self.assertContains(response, "data-drag-handle", count=4)

    def test_dashboard_uses_public_home_and_simulation_hides_internal_duration(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.staff)

        response = self.client.get("/dashboard/")
        simulation_template = (
            Path(__file__).parents[3] / "templates/qbet_web/simulation.html"
        ).read_text(encoding="utf-8")

        self.assertContains(response, 'href="/" aria-label="Q-Bet home"')
        self.assertContains(response, "Sandbox simulation")
        self.assertNotContains(response, "Max duration")
        self.assertNotIn("max_duration_minutes", simulation_template)

    def test_dashboard_drag_script_finishes_on_escape_and_pointer_loss(self) -> None:
        script = (Path(__file__).parents[3] / "static/qbet_web/dashboard.js").read_text(
            encoding="utf-8"
        )

        self.assertIn('addEventListener("lostpointercapture", finishPointerDrag)', script)
        self.assertIn('window.addEventListener("blur"', script)
        self.assertIn('event.key === "Escape"', script)

    def test_narrow_viewport_keeps_keyboard_reorder_handles_visible(self) -> None:
        stylesheet = (Path(__file__).parents[3] / "static/qbet_web/app.css").read_text(
            encoding="utf-8"
        )
        narrow_viewport_rules = stylesheet.split("@media (max-width: 760px)", maxsplit=1)[1]
        narrow_viewport_rules = narrow_viewport_rules.split(
            "@media (max-width: 440px)", maxsplit=1
        )[0]

        self.assertNotIn(".drag-handle { display: none; }", narrow_viewport_rules)
