from django.contrib.auth.models import User
from django.test import TestCase

from qbet.simulation import SimulationEngine
from qbet.web.models import SimulationAvailability, SimulationRunState


class SimulationReportRedirectTests(TestCase):
    def setUp(self) -> None:
        self.staff = User.objects.create_user(
            "staff-78",
            password="Strong-pass-123",
            is_staff=True,
        )
        SimulationAvailability.objects.all().delete()
        SimulationRunState.objects.all().delete()

    def test_successful_gui_start_redirects_to_persisted_report_detail(self) -> None:
        SimulationAvailability.objects.create(pk=1, enabled=True)
        self.client.force_login(self.staff)

        response = self.client.post(
            "/simulation/start/",
            {
                "engine": SimulationEngine.BONUS.value,
                "starting_capital": "100.00",
                "max_duration_minutes": "60",
            },
        )

        run = SimulationRunState.objects.get()
        self.assertIsNotNone(run.report_id)
        assert run.report_id is not None
        self.assertRedirects(
            response,
            f"/reports/{run.report_id}/",
            fetch_redirect_response=False,
        )
