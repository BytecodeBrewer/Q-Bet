from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.bank import BunqConfigurationError
from qbet.domain.ledger import PortfolioBalance
from qbet.ledger import PortfolioLedger
from qbet.storage.models import PortfolioLedgerRow, SportsbookProviderRow
from qbet.web.models import PortfolioCapitalLocation, PortfolioLedgerAccess
from qbet.web.portfolio import PortfolioCapitalReadService
from qbet.web.portfolio_locations import CentralAccountBalance


class PortfolioCapitalTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("member", password="Strong-pass-123")
        self.other = User.objects.create_user("other", password="Strong-pass-123")
        self.staff = User.objects.create_user("staff", password="Strong-pass-123", is_staff=True)
        self.provider = SportsbookProviderRow.objects.create(
            provider_id="licensed-book",
            legal_name="Licensed Book GmbH",
            display_name="Licensed Book",
            jurisdiction="DE",
            sports_betting=True,
            online=True,
            source_url="https://example.invalid",
            whitelist_snapshot_date="2026-09-07",
            status="active",
        )

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
            mode=mode, currency=currency, payload=ledger.model_dump(mode="json")
        )

    def _grant(self, user: User, mode: str, currency: str) -> None:
        PortfolioLedgerAccess.objects.create(user=user, mode=mode, currency=currency)

    def test_provider_location_partitions_ledger_and_central_account_is_remainder(self) -> None:
        self._store("execution", "EUR", available="700", reserved="40", locked="20", pending="30")
        self._grant(self.user, "execution", "EUR")
        PortfolioCapitalLocation.objects.create(
            user=self.user,
            provider=self.provider,
            mode="execution",
            currency="EUR",
            amount=Decimal("170"),
            note="Checked against provider balance",
        )

        snapshot = PortfolioCapitalReadService().snapshot(user_id=self.user.pk)
        central, provider = snapshot.execution

        self.assertEqual(central.label, "bunq")
        self.assertEqual(central.amount, Decimal("620"))
        self.assertEqual(provider.label, "Licensed Book")
        self.assertEqual(provider.amount, Decimal("170"))
        self.assertEqual(provider.status, "recorded")
        self.assertEqual(snapshot.execution_totals[0].tracked_total, Decimal("790"))
        self.assertEqual(snapshot.execution_totals[0].in_use, Decimal("50"))

    def test_execution_lists_all_active_online_german_sportsbooks(self) -> None:
        second = SportsbookProviderRow.objects.create(
            provider_id="another-book",
            legal_name="Another Book GmbH",
            display_name="Another Book",
            jurisdiction="DE",
            sports_betting=True,
            online=True,
            source_url="https://another.invalid",
            whitelist_snapshot_date="2026-09-07",
            status="active",
        )
        SportsbookProviderRow.objects.create(
            provider_id="inactive-book",
            legal_name="Inactive Book GmbH",
            display_name="Inactive Book",
            jurisdiction="DE",
            sports_betting=True,
            online=True,
            source_url="https://inactive.invalid",
            whitelist_snapshot_date="2026-09-07",
            status="inactive",
        )
        SportsbookProviderRow.objects.create(
            provider_id="foreign-book",
            legal_name="Foreign Book Ltd",
            display_name="Foreign Book",
            jurisdiction="GB",
            sports_betting=True,
            online=True,
            source_url="https://foreign.invalid",
            whitelist_snapshot_date="2026-09-07",
            status="active",
        )
        self._store("execution", "EUR", available="100")
        self._grant(self.user, "execution", "EUR")

        snapshot = PortfolioCapitalReadService().snapshot(user_id=self.user.pk)
        providers = [item for item in snapshot.execution if item.kind == "provider"]

        self.assertEqual([item.label for item in providers], ["Another Book", "Licensed Book"])
        self.assertTrue(all(item.amount is None for item in providers))
        self.assertTrue(all(item.status == "not_recorded" for item in providers))
        self.assertIn(second.provider_id, [item.provider_id for item in providers])

    def test_location_overallocation_fails_closed(self) -> None:
        self._store("execution", "EUR", available="100")
        self._grant(self.user, "execution", "EUR")
        PortfolioCapitalLocation.objects.create(
            user=self.user,
            provider=self.provider,
            mode="execution",
            currency="EUR",
            amount=Decimal("101"),
        )

        snapshot = PortfolioCapitalReadService().snapshot(user_id=self.user.pk)

        self.assertFalse(snapshot.available)
        self.assertIn("exceed", snapshot.message or "")

    def test_currency_and_mode_boundaries_remain_separate(self) -> None:
        self._store("execution", "EUR", available="100")
        self._store("execution", "USD", available="200")
        self._store("simulation", "EUR", available="900")
        for mode, currency in (
            ("execution", "EUR"),
            ("execution", "USD"),
            ("simulation", "EUR"),
        ):
            self._grant(self.user, mode, currency)

        snapshot = PortfolioCapitalReadService().snapshot(user_id=self.user.pk)

        self.assertEqual(
            [(item.currency, item.tracked_total) for item in snapshot.execution_totals],
            [("EUR", Decimal("100")), ("USD", Decimal("200"))],
        )
        self.assertEqual(snapshot.simulation_totals[0].tracked_total, Decimal("900"))

    def test_cross_user_provider_amount_is_not_visible(self) -> None:
        self._store("execution", "EUR", available="125")
        self._grant(self.user, "execution", "EUR")
        self._grant(self.other, "execution", "EUR")
        PortfolioCapitalLocation.objects.create(
            user=self.other,
            provider=self.provider,
            mode="execution",
            currency="EUR",
            amount=Decimal("50"),
        )

        snapshot = PortfolioCapitalReadService().snapshot(user_id=self.user.pk)
        central = next(item for item in snapshot.execution if item.kind == "central")
        provider = next(item for item in snapshot.execution if item.kind == "provider")

        self.assertEqual(central.amount, Decimal("125"))
        self.assertEqual(provider.label, "Licensed Book")
        self.assertIsNone(provider.amount)
        self.assertEqual(provider.status, "not_recorded")

    def test_page_uses_plain_account_copy_and_consistent_provider_controls(self) -> None:
        second = SportsbookProviderRow.objects.create(
            provider_id="another-book",
            legal_name="Another Book GmbH",
            display_name="Another Book",
            jurisdiction="DE",
            sports_betting=True,
            online=True,
            source_url="https://another.invalid",
            whitelist_snapshot_date="2026-09-07",
            status="active",
        )
        self._store("execution", "EUR", available="125", reserved="20", locked="5", pending="3")
        self._grant(self.user, "execution", "EUR")
        PortfolioCapitalLocation.objects.create(
            user=self.user,
            provider=self.provider,
            mode="execution",
            currency="EUR",
            amount=Decimal("40"),
        )
        self.client.force_login(self.user)

        response = self.client.get("/portfolio/")

        self.assertContains(response, "Total capital")
        self.assertContains(response, "Central account")
        self.assertContains(response, "bunq")
        self.assertContains(response, "Providers")
        self.assertContains(response, "Licensed Book")
        self.assertContains(response, second.display_name)
        self.assertContains(response, "Free")
        self.assertContains(response, "Reserved")
        self.assertContains(response, "In use")
        self.assertContains(response, 'data-provider-edit')
        self.assertContains(response, 'aria-label="Edit balance for Licensed Book"')
        self.assertContains(response, 'aria-label="Edit balance for Another Book"')
        self.assertContains(response, "Search providers")
        self.assertContains(response, 'data-provider-sort')
        self.assertContains(response, 'data-provider-page-size')
        self.assertContains(response, '<option value="4" selected>4</option>', html=True)
        self.assertContains(response, '<option value="8">8</option>', html=True)
        self.assertContains(response, '<option value="12">12</option>', html=True)
        self.assertContains(response, "Load more")
        self.assertContains(response, "Refresh")
        self.assertContains(response, "Details")
        self.assertNotContains(response, "Set balance")
        self.assertNotContains(response, "Edit locations")
        self.assertNotContains(response, ">Manual<")
        self.assertNotContains(response, "Where the money is")
        self.assertNotContains(response, ">Locked<")
        self.assertNotContains(response, ">Pending<")

    def test_provider_edit_updates_balance_without_changing_ledger_total(self) -> None:
        self._store("execution", "EUR", available="125", reserved="20")
        self._grant(self.user, "execution", "EUR")
        self.client.force_login(self.user)

        response = self.client.post(
            "/portfolio/locations/update/",
            {
                "provider": self.provider.provider_id,
                "currency": "EUR",
                "amount": "60",
                "note": "Reconciled balance",
            },
        )

        self.assertRedirects(response, "/portfolio/")
        location = PortfolioCapitalLocation.objects.get(user=self.user, provider=self.provider)
        self.assertEqual(location.amount, Decimal("60"))
        ledger = PortfolioLedger.model_validate(
            PortfolioLedgerRow.objects.get(mode="execution", currency="EUR").payload
        )
        self.assertEqual(ledger.balance.available, Decimal("125"))
        self.assertEqual(ledger.balance.reserved, Decimal("20"))

    def test_provider_edit_cannot_allocate_more_than_tracked_capital(self) -> None:
        self._store("execution", "EUR", available="25")
        self._grant(self.user, "execution", "EUR")
        self.client.force_login(self.user)

        self.client.post(
            "/portfolio/locations/update/",
            {
                "provider": self.provider.provider_id,
                "currency": "EUR",
                "amount": "26",
                "note": "",
            },
        )

        self.assertFalse(PortfolioCapitalLocation.objects.exists())

    @patch("qbet.web.portfolio_locations.read_bunq_balance")
    def test_central_refresh_returns_fresh_bunq_balance_without_mutating_ledger(
        self, read_balance
    ) -> None:
        self._store("execution", "EUR", available="125", reserved="20")
        self._grant(self.user, "execution", "EUR")
        self.client.force_login(self.user)
        observed_at = datetime(2026, 9, 29, 10, 15, tzinfo=UTC)
        read_balance.return_value = CentralAccountBalance(
            amount=Decimal("321.45"),
            currency="EUR",
            observed_at=observed_at,
        )

        response = self.client.post("/portfolio/central/refresh/", {"currency": "EUR"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "amount": "321.45",
                "currency": "EUR",
                "observed_at": observed_at.isoformat(),
            },
        )
        ledger = PortfolioLedger.model_validate(
            PortfolioLedgerRow.objects.get(mode="execution", currency="EUR").payload
        )
        self.assertEqual(ledger.balance.available, Decimal("125"))
        self.assertEqual(ledger.balance.reserved, Decimal("20"))

    @patch("qbet.web.portfolio_locations.read_bunq_balance")
    def test_central_refresh_fails_closed_when_bunq_is_not_configured(self, read_balance) -> None:
        self._store("execution", "EUR", available="125")
        self._grant(self.user, "execution", "EUR")
        self.client.force_login(self.user)
        read_balance.side_effect = BunqConfigurationError("missing configuration")

        response = self.client.post("/portfolio/central/refresh/", {"currency": "EUR"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")
        self.assertNotIn("missing configuration", response.content.decode())

    @patch("qbet.web.portfolio_locations.read_bunq_balance")
    def test_ungranted_user_cannot_refresh_central_balance(self, read_balance) -> None:
        self._store("execution", "EUR", available="125")
        self._grant(self.other, "execution", "EUR")
        self.client.force_login(self.user)

        response = self.client.post("/portfolio/central/refresh/", {"currency": "EUR"})

        self.assertEqual(response.status_code, 403)
        read_balance.assert_not_called()

    def test_ungranted_user_sees_no_capital_amount_or_provider_cards(self) -> None:
        self._store("execution", "EUR", available="125")
        self._grant(self.other, "execution", "EUR")
        PortfolioCapitalLocation.objects.create(
            user=self.other,
            provider=self.provider,
            mode="execution",
            currency="EUR",
            amount=Decimal("50"),
        )
        self.client.force_login(self.user)

        response = self.client.get("/portfolio/")

        self.assertNotContains(response, "125,00 EUR")
        self.assertNotContains(response, "data-provider-card")
        self.assertContains(response, "No Execution capital assigned")
        portfolio = PortfolioCapitalReadService().snapshot(user_id=self.user.pk)
        self.assertFalse(portfolio.execution)
        self.assertFalse(portfolio.execution_totals)

    def test_normal_user_does_not_receive_simulation_plane_but_staff_does(self) -> None:
        self._store("simulation", "EUR", available="900")
        self._grant(self.user, "simulation", "EUR")
        self.client.force_login(self.user)

        response = self.client.get("/portfolio/")

        self.assertNotContains(response, "Sandbox")
        self.assertNotContains(response, "900,00 EUR")

        self.client.force_login(self.staff)
        response = self.client.get("/portfolio/")
        self.assertContains(response, "Sandbox")
        self.assertContains(response, "900,00 EUR")
