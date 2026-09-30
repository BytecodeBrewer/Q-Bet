"""User-facing capital-location read model over authoritative PortfolioLedger state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import DatabaseError
from pydantic import ValidationError

from qbet.ledger import PortfolioLedger
from qbet.providers import GERMAN_JURISDICTION, ProviderStatus
from qbet.storage.models import PortfolioLedgerRow, SportsbookProviderRow
from qbet.web.models import PortfolioCapitalLocation, PortfolioLedgerAccess


@dataclass(frozen=True)
class CapitalLocation:
    kind: str
    mode: str
    currency: str
    label: str
    amount: Decimal | None
    updated_at: datetime | None
    source: str
    status: str
    provider_id: str | None = None
    note: str = ""
    available: Decimal | None = None
    reserved: Decimal | None = None
    locked: Decimal | None = None
    pending: Decimal | None = None


@dataclass(frozen=True)
class CurrencyTotals:
    currency: str
    tracked_total: Decimal
    available: Decimal
    reserved: Decimal
    locked: Decimal
    pending: Decimal

    @property
    def in_use(self) -> Decimal:
        """Customer-facing active capital combines dispatched and unsettled amounts."""

        return self.locked + self.pending


@dataclass(frozen=True)
class PortfolioCapitalView:
    execution: tuple[CapitalLocation, ...] = ()
    simulation: tuple[CapitalLocation, ...] = ()
    execution_totals: tuple[CurrencyTotals, ...] = ()
    simulation_totals: tuple[CurrencyTotals, ...] = ()
    available: bool = True
    message: str | None = None


class PortfolioCapitalReadService:
    """Expose ledger totals and account/provider capital locations."""

    def snapshot(self, *, user_id: int, is_staff: bool = False) -> PortfolioCapitalView:
        try:
            rows_query = PortfolioLedgerRow.objects.order_by("mode", "currency")
            if not is_staff:
                allowed = set(
                    PortfolioLedgerAccess.objects.filter(user_id=user_id).values_list(
                        "mode", "currency"
                    )
                )
                rows = tuple(row for row in rows_query if (row.mode, row.currency) in allowed)
            else:
                rows = tuple(rows_query)
            allocations = tuple(
                PortfolioCapitalLocation.objects.filter(user_id=user_id).select_related("provider")
            )
            providers = tuple(
                SportsbookProviderRow.objects.filter(
                    jurisdiction=GERMAN_JURISDICTION,
                    sports_betting=True,
                    online=True,
                    status=ProviderStatus.ACTIVE.value,
                ).order_by("display_name", "provider_id")
            )
        except DatabaseError:
            return PortfolioCapitalView(
                available=False, message="Portfolio capital is temporarily unavailable."
            )

        execution: list[CapitalLocation] = []
        simulation: list[CapitalLocation] = []
        execution_totals: list[CurrencyTotals] = []
        simulation_totals: list[CurrencyTotals] = []
        try:
            for row in rows:
                ledger = PortfolioLedger.model_validate(row.payload)
                balance = ledger.balance
                tracked_total = (
                    balance.available + balance.reserved + balance.locked + balance.pending
                )
                totals = CurrencyTotals(
                    currency=balance.currency,
                    tracked_total=tracked_total,
                    available=balance.available,
                    reserved=balance.reserved,
                    locked=balance.locked,
                    pending=balance.pending,
                )
                (execution_totals if balance.mode == "execution" else simulation_totals).append(totals)

                matching = [
                    item
                    for item in allocations
                    if item.mode == balance.mode and item.currency == balance.currency
                ]
                allocations_by_provider = {item.provider.provider_id: item for item in matching}
                provider_total = sum((item.amount for item in matching), Decimal(0))
                if provider_total > tracked_total:
                    return PortfolioCapitalView(
                        available=False,
                        message="Recorded provider balances exceed total portfolio capital.",
                    )

                target = execution if balance.mode == "execution" else simulation
                unallocated = tracked_total - provider_total
                if balance.mode == "execution":
                    target.append(
                        CapitalLocation(
                            kind="central",
                            mode=balance.mode,
                            currency=balance.currency,
                            label="bunq",
                            amount=None,
                            updated_at=None,
                            source="bunq read-only balance",
                            status="unavailable",
                        )
                    )
                target.append(
                    CapitalLocation(
                        kind="unallocated",
                        mode=balance.mode,
                        currency=balance.currency,
                        label=(
                            "Unallocated capital"
                            if balance.mode == "execution"
                            else "Unallocated simulation capital"
                        ),
                        amount=unallocated,
                        updated_at=row.updated_at,
                        source="Q-Bet ledger allocation",
                        status="recorded",
                        available=balance.available,
                        reserved=balance.reserved,
                        locked=balance.locked,
                        pending=balance.pending,
                    )
                )

                visible_providers = (
                    providers
                    if balance.mode == "execution"
                    else tuple(item.provider for item in matching)
                )
                for provider in visible_providers:
                    item = allocations_by_provider.get(provider.provider_id)
                    target.append(
                        CapitalLocation(
                            kind="provider",
                            mode=balance.mode,
                            currency=balance.currency,
                            label=provider.display_name,
                            amount=item.amount if item is not None else None,
                            updated_at=item.updated_at if item is not None else None,
                            source="Entered in Q-Bet" if item is not None else "",
                            status="recorded" if item is not None else "not_recorded",
                            provider_id=provider.provider_id,
                            note=item.note if item is not None else "",
                        )
                    )
        except (ValidationError, ValueError, AttributeError):
            return PortfolioCapitalView(
                available=False,
                message="Portfolio capital could not be verified from the authoritative ledger.",
            )

        return PortfolioCapitalView(
            execution=tuple(execution),
            simulation=tuple(simulation),
            execution_totals=tuple(execution_totals),
            simulation_totals=tuple(simulation_totals),
        )
