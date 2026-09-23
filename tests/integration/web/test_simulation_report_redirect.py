from django.contrib.auth.models import User
from django.test import TestCase

from qbet.simulation import SimulationEngine
from qbet.storage.ledger import RoutingConfigurationRepository
from qbet.web.models import SimulationAvailability, SimulationRunState
from qbet.workflow.routing import EngineModes, RoutingConfiguration


class SimulationReportRedirectTests(TestCase):
    def setUp(self) -> None:
        self.staff = User.objects.create_user(
            "staff-78",
            password="Strong-pass-123",
            is_staff=True,
        )
        SimulationAvailability.objects.all().delete()
        SimulationRunState.objects.all().delete()

    def test_successful_gui_start_exposes_running_state_then_returns_report_url(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        RoutingConfigurationRepository().save(
            RoutingConfiguration(sports_capital=EngineModes(simulation=True))
        )
        self.client.force_login(self.staff)

        response = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.SPORTS_CAPITAL.value,
                "starting_capital": "100.00",
            },
        )

        run = SimulationRunState.objects.get()
        self.assertEqual(run.status, SimulationRunState.Status.RUNNING)
        self.assertIsNone(run.report_id)
        self.assertRedirects(
            response,
            f"/simulation/?autostart={run.run_id}",
            fetch_redirect_response=False,
        )

        executed = self.client.post(f"/simulation/{run.run_id}/run/")
        run.refresh_from_db()

        self.assertEqual(executed.status_code, 200)
        self.assertIsNotNone(run.report_id)
        assert run.report_id is not None
        self.assertEqual(executed.json()["report_url"], f"/reports/{run.report_id}/")
