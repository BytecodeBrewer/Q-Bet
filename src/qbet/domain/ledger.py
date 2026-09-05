"""Immutable, mode-isolated capital balances."""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from qbet.domain.models import Currency, DomainModel

FiniteBalance = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class PortfolioBalance(DomainModel):
    mode: Literal["simulation", "execution"]
    currency: Currency
    available: FiniteBalance = Decimal(0)
    reserved: FiniteBalance = Decimal(0)
    locked: FiniteBalance = Decimal(0)
    pending: FiniteBalance = Decimal(0)
    settled: FiniteBalance = Decimal(0)
    cost: FiniteBalance = Decimal(0)


class LedgerOperation(StrEnum):
    RESERVE = "reserve"
    RELEASE = "release"
    LOCK = "lock"
    PENDING = "pending"
    SETTLE = "settle"
    FAIL = "fail"
    COST = "cost"


class LedgerCommand(DomainModel):
    id: str = Field(min_length=1)
    dispatch_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    currency: Currency
    operation: LedgerOperation
    amount: FiniteBalance


class LedgerDecision(DomainModel):
    accepted: bool
    balance: PortfolioBalance
    reason: str | None = None
    duplicate: bool = False
