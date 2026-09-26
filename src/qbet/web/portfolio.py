"""User-facing read model for authoritative portfolio capital state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from django.db import DatabaseError
from pydantic import ValidationError

from qbet.ledger import PortfolioLedger
from qbet.storage.models import PortfolioLedgerRow


@dataclass(frozen=True)
class CapitalLocation:
    mode: str
    currency: str
    label: str
    available: Decimal
    reserved: Decimal
    working: Decimal
    pending: Decimal
    settled: Decimal
    cost: Decimal
    tracked_total: Decimal
    updated_at: datetime
    source: str = "PortfolioLedger"
    status: str = "verified"


@dataclass(frozen=True)
class CurrencyTotals:
    currency: str
    tracked_total: Decimal
    available: Decimal
    reserved: Decimal
    working: Decimal
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
    """Read persisted ledgers without deriving balances from engines or reports."""

    def snapshot(self) -> PortfolioCapitalView:
        try:
            rows = tuple(PortfolioLedgerRow.objects.order_by("mode", "currency"))
        except DatabaseError:
            return PortfolioCapitalView(
                available=False,
                message="Portfolio capital is temporarily unavailable.",
            )

        execution: list[CapitalLocation] = []
        simulation: list[CapitalLocation] = []
        try:
            for row in rows:
                ledger = PortfolioLedger.model_validate(row.payload)
                balance = ledger.balance
                # settled and cost are cumulative lifecycle counters. They are deliberately
                # excluded from tracked_total so capital is never counted twice.
                tracked_total = (
                    balance.available + balance.reserved + balance.locked + balance.pending
                )
                location = CapitalLocation(
                    mode=balance.mode,
                    currency=balance.currency,
                    label=(
                        "Execution portfolio"
                        if balance.mode == "execution"
                        else "Simulation sandbox"
                    ),
                    available=balance.available,
                    reserved=balance.reserved,
                    working=balance.locked,
                    pending=balance.pending,
                    settled=balance.settled,
                    cost=balance.cost,
                    tracked_total=tracked_total,
                    updated_at=row.updated_at,
                )
                (execution if balance.mode == "execution" else simulation).append(location)
        except (ValidationError, ValueError, AttributeError):
            return PortfolioCapitalView(
                available=False,
                message="Portfolio capital could not be verified from the authoritative ledger.",
            )

        return PortfolioCapitalView(
            execution=tuple(execution),
            simulation=tuple(simulation),
            execution_totals=_totals(execution),
            simulation_totals=_totals(simulation),
        )


def _totals(locations: list[CapitalLocation]) -> tuple[CurrencyTotals, ...]:
    currencies = sorted({location.currency for location in locations})
    return tuple(
        CurrencyTotals(
            currency=currency,
            tracked_total=sum(
                (item.tracked_total for item in locations if item.currency == currency),
                Decimal(0),
            ),
            available=sum(
                (item.available for item in locations if item.currency == currency), Decimal(0)
            ),
            reserved=sum(
                (item.reserved for item in locations if item.currency == currency), Decimal(0)
            ),
            working=sum(
                (item.working for item in locations if item.currency == currency), Decimal(0)
            ),
            pending=sum(
                (item.pending for item in locations if item.currency == currency), Decimal(0)
            ),
        )
        for currency in currencies
    )
