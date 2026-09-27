from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.domain.ledger import PortfolioBalance
from qbet.ledger import PortfolioLedger
from qbet.storage.models import PortfolioLedgerRow
from qbet.web.models import PortfolioLedgerAccess
from qbet.web.portfolio import PortfolioCapitalReadService


class PortfolioCapitalTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("member", password="Strong-pass-123")
        self.other = User.objects.create_user("other", password="Strong-pass-123")
        self.staff = User.objects.create_user("staff", password="Strong-pass-123", is_staff=True)

    def _store(
        self,
        mode: str,
        currency: str,
        *,
        available: str,
        reserved: str = "0",
        locked: str = "0",
        pending: str = "0",
        settled: str = "0",
        cost: str = "0",
    ) -> None:
        ledger = PortfolioLedger(
            balance=PortfolioBalance(
                mode=mode,
                currency=currency,
                available=Decimal(available),
                reserved=Decimal(reserved),
                locked=Decimal(locked),
                pending=Decimal(pending),
                settled=Decimal(settled),
                cost=Decimal(cost),
            )
        )
        PortfolioLedgerRow.objects.create(
            mode=mode,
            currency=currency,
            payload=ledger.model_dump(mode="json"),
        )

    def _grant(self, user: User, mode: str, currency: str) -> None:
        PortfolioLedgerAccess.objects.create(user=user, mode=mode, currency=currency)

    def test_totals_count_current_capital_once_and_preserve_locked_state(self) -> None:
        self._store(
            "execution",
            "EUR",
            available="700",
            reserved="40",
            locked="20",
            pending="30",
            settled="500",
            cost="10",
        )
        self._grant(self.user, "execution", "EUR")

        snapshot = PortfolioCapitalReadService().snapshot(user_id=self.user.pk)

        total = snapshot.execution_totals[0]
        self.assertEqual(total.tracked_total, Decimal("790"))
        self.assertEqual(total.available, Decimal("700"))
        self.assertEqual(total.reserved, Decimal("40"))
        self.assertEqual(total.locked, Decimal("20"))
        self.assertEqual(total.pending, Decimal("30"))

    def test_currency_and_mode_boundaries_are_not_combined(self) -> None:
        self._store("execution", "EUR", available="100")
        self._store("execution", "USD", available="200")
        self._store("simulation", "EUR", available="900")
        for mode, currency in (("execution", "EUR"), ("execution", "USD"), ("simulation", "EUR")):
            self._grant(self.user, mode, currency)

        snapshot = PortfolioCapitalReadService().snapshot(user_id=self.user.pk)

        self.assertEqual(
            [(item.currency, item.tracked_total) for item in snapshot.execution_totals],
            [("EUR", Decimal("100")), ("USD", Decimal("200"))],
        )
        self.assertEqual(snapshot.simulation_totals[0].tracked_total, Decimal("900"))

    def test_user_can_read_only_explicitly_granted_ledger_contexts(self) -> None:
        self._store("execution", "EUR", available="125")
        self._store("simulation", "EUR", available="900")
        self._grant(self.user, "execution", "EUR")
        self._grant(self.other, "simulation", "EUR")

        member = PortfolioCapitalReadService().snapshot(user_id=self.user.pk)
        other = PortfolioCapitalReadService().snapshot(user_id=self.other.pk)

        self.assertEqual(member.execution_totals[0].available, Decimal("125"))
        self.assertFalse(member.simulation)
        self.assertFalse(other.execution)
        self.assertEqual(other.simulation_totals[0].available, Decimal("900"))

    def test_staff_read_is_explicitly_broader(self) -> None:
        self._store("execution", "EUR", available="125")
        snapshot = PortfolioCapitalReadService().snapshot(user_id=self.staff.pk, is_staff=True)
        self.assertEqual(snapshot.execution_totals[0].available, Decimal("125"))

    def test_portfolio_page_is_authenticated_and_hides_ungranted_capital(self) -> None:
        self._store("execution", "EUR", available="125")
        self.assertRedirects(
            self.client.get("/portfolio/"),
            "/accounts/login/?next=/portfolio/",
            fetch_redirect_response=False,
        )

        self.client.force_login(self.user)
        hidden = self.client.get("/portfolio/")
        self.assertEqual(hidden.status_code, 200)
        self.assertNotContains(hidden, "125,00 EUR")
        self.assertContains(hidden, "Execution capital is not available for this account")
        self.assertNotContains(hidden, "no live-capital context")

        self._grant(self.user, "execution", "EUR")
        visible = self.client.get("/portfolio/")
        self.assertContains(visible, "125,00 EUR")
        self.assertContains(visible, "Locked")
        self.assertContains(visible, "Account-level balances are not configured yet")

    def test_normal_user_does_not_receive_simulation_plane_but_staff_does(self) -> None:
        self._store("simulation", "EUR", available="900")
        self._grant(self.user, "simulation", "EUR")

        self.client.force_login(self.user)
        response = self.client.get("/portfolio/")
        self.assertNotContains(response, "Sandbox only")
        self.assertNotContains(response, "900,00 EUR")

        self.client.force_login(self.staff)
        response = self.client.get("/portfolio/")
        self.assertContains(response, "Sandbox only")
        self.assertContains(response, "900,00 EUR")
