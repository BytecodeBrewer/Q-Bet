from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from django.contrib.auth.models import User
from django.test import TestCase

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

    def load_records(self, run_id: UUID) -> tuple[()]:
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

        from unittest.mock import patch

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
        self.assertContains(response, "Drag the handles or use arrow keys to reorder.")
