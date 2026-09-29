"""User-facing capital-location read model over authoritative PortfolioLedger state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import DatabaseError
from pydantic import ValidationError

from qbet.ledger import PortfolioLedger
from qbet.storage.models import PortfolioLedgerRow
from qbet.web.models import PortfolioCapitalLocation, PortfolioLedgerAccess


@dataclass(frozen=True)
class CapitalLocation:
    kind: str
    mode: str
    currency: str
    label: str
    amount: Decimal
    updated_at: datetime
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

@dataclass(frozen=True)
class PortfolioCapitalView:
    execution: tuple[CapitalLocation, ...] = ()
    simulation: tuple[CapitalLocation, ...] = ()
    execution_totals: tuple[CurrencyTotals, ...] = ()
    simulation_totals: tuple[CurrencyTotals, ...] = ()
    available: bool = True
    message: str | None = None


class PortfolioCapitalReadService:
    """Expose ledger lifecycle totals and independently tracked capital locations."""

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
                    item for item in allocations
                    if item.mode == balance.mode and item.currency == balance.currency
                ]
                provider_total = sum((item.amount for item in matching), Decimal(0))
                if provider_total > tracked_total:
                    return PortfolioCapitalView(
                        available=False,
                        message="Capital locations exceed the authoritative ledger and require correction.",
                    )

                target = execution if balance.mode == "execution" else simulation
                target.append(
                    CapitalLocation(
                        kind="central", mode=balance.mode, currency=balance.currency,
                        label="Central payment account", amount=tracked_total - provider_total,
                        updated_at=row.updated_at,
                        source="PortfolioLedger · residual after provider locations",
                        status="verified",
                        available=balance.available,
                        reserved=balance.reserved,
                        locked=balance.locked,
                        pending=balance.pending,
                    )
                )
                for item in matching:
                    target.append(
                        CapitalLocation(
                            kind="provider", mode=item.mode, currency=item.currency,
                            label=item.provider.display_name, amount=item.amount,
                            updated_at=item.updated_at, source="Manual location correction",
                            status="manual", provider_id=item.provider.provider_id, note=item.note,
                        )
                    )
        except (ValidationError, ValueError, AttributeError):
            return PortfolioCapitalView(
                available=False,
                message="Portfolio capital could not be verified from the authoritative ledger.",
            )

        return PortfolioCapitalView(
            execution=tuple(execution), simulation=tuple(simulation),
            execution_totals=tuple(execution_totals),
            simulation_totals=tuple(simulation_totals),
        )
