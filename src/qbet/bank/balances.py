"""Provider-neutral, read-only balance normalization before capital consumers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, TypeAlias, runtime_checkable
from uuid import UUID

from pydantic import ValidationError, field_validator, model_validator

from qbet.data.models import DataSourceMetadata, FreshnessStatus
from qbet.domain.models import Currency, DomainModel, Identifier, NonNegativeDecimal


class BankBalanceStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    INVALID = "invalid"
    MISMATCHED = "mismatched"
    CURRENCY_MISMATCH = "currency_mismatch"


class BankBalanceRequest(DomainModel):
    """Selected read-only balance lookup context, including required identity checks."""

    source: DataSourceMetadata
    account_reference: Identifier
    currency: Currency
    correlation_id: UUID
    fresh_after: datetime

    @field_validator("account_reference")
    @classmethod
    def account_reference_is_redacted(cls, value: str) -> str:
        if "*" not in value:
            raise ValueError("account_reference must be redacted")
        return value

    @field_validator("fresh_after")
    @classmethod
    def fresh_after_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fresh_after must include timezone information")
        return value


class BankBalance(DomainModel):
    """Normalized observed balance with no authority to alter capital state."""

    source: DataSourceMetadata
    account_reference: Identifier
    currency: Currency
    available_balance: NonNegativeDecimal
    current_balance: NonNegativeDecimal | None = None
    observed_at: datetime
    freshness: FreshnessStatus
    correlation_id: UUID

    @field_validator("account_reference")
    @classmethod
    def account_reference_is_redacted(cls, value: str) -> str:
        if "*" not in value:
            raise ValueError("account_reference must be redacted")
        return value

    @field_validator("available_balance", "current_balance")
    @classmethod
    def balances_are_finite(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("balance amounts must be finite")
        return value

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include timezone information")
        return value


class BankBalanceProviderResponse(DomainModel):
    """A provider response before request-boundary validation."""

    status: BankBalanceStatus
    balance: BankBalance | None = None
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_provider_response(self) -> "BankBalanceProviderResponse":
        if self.status is BankBalanceStatus.AVAILABLE:
            if self.balance is None or self.reason_code is not None:
                raise ValueError("available provider responses require a balance only")
        elif self.balance is not None or self.reason_code is None:
            raise ValueError("unavailable provider responses require a reason_code only")
        return self


class BankBalanceOutcome(DomainModel):
    """Stable application outcome before any future capital-coverage consumer."""

    request: BankBalanceRequest
    status: BankBalanceStatus
    balance: BankBalance | None = None
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_outcome(self) -> "BankBalanceOutcome":
        if self.status is BankBalanceStatus.AVAILABLE:
            if self.balance is None or self.reason_code is not None:
                raise ValueError("available outcomes require a balance only")
        elif self.balance is not None or self.reason_code is None:
            raise ValueError("non-available outcomes require a reason_code only")
        return self

    def require_fresh_balance(self) -> BankBalance:
        """Reject any non-available value before a future liquidity consumer uses it."""

        if self.status is not BankBalanceStatus.AVAILABLE or self.balance is None:
            raise ValueError("bank balance outcome is not available")
        return self.balance


ProviderBalanceResponse: TypeAlias = BankBalanceProviderResponse | Mapping[str, object]


@runtime_checkable
class BankBalanceProvider(Protocol):
    """Read-only adapter contract; it deliberately has no account mutation operation."""

    def read_balance(self, request: BankBalanceRequest) -> ProviderBalanceResponse: ...


class BankBalanceFixture(DomainModel):
    """Deterministic provider response keyed by provider/source and redacted account."""

    source: DataSourceMetadata
    account_reference: Identifier
    response: ProviderBalanceResponse

    @field_validator("account_reference")
    @classmethod
    def account_reference_is_redacted(cls, value: str) -> str:
        if "*" not in value:
            raise ValueError("account_reference must be redacted")
        return value


class DeterministicBankBalanceProvider:
    """Fixture-backed provider for deterministic sandbox and integration tests."""

    def __init__(self, fixtures: tuple[BankBalanceFixture, ...] = ()) -> None:
        indexed: dict[tuple[DataSourceMetadata, str], ProviderBalanceResponse] = {}
        for fixture in fixtures:
            key = (fixture.source, fixture.account_reference)
            if key in indexed:
                raise ValueError(
                    "bank balance fixtures must use distinct source/account references"
                )
            indexed[key] = fixture.response
        self._fixtures = indexed

    def read_balance(self, request: BankBalanceRequest) -> ProviderBalanceResponse:
        return self._fixtures.get(
            (request.source, request.account_reference),
            BankBalanceProviderResponse(
                status=BankBalanceStatus.UNAVAILABLE,
                reason_code="balance_not_available",
            ),
        )


class ReadOnlyBankBalanceService:
    """Application boundary that validates a provider response without retaining or mutating it."""

    def __init__(self, provider: BankBalanceProvider) -> None:
        self._provider = provider

    def read_balance(self, request: BankBalanceRequest) -> BankBalanceOutcome:
        try:
            response = BankBalanceProviderResponse.model_validate(
                self._provider.read_balance(request)
            )
        except ValidationError:
            return _outcome(request, BankBalanceStatus.INVALID, "balance_response_invalid")

        if response.status is not BankBalanceStatus.AVAILABLE:
            return _outcome(request, response.status, response.reason_code)

        balance = response.balance
        assert balance is not None
        if not _matches_request(balance, request):
            return _outcome(request, BankBalanceStatus.MISMATCHED, "balance_identity_mismatch")
        if balance.currency != request.currency:
            return _outcome(
                request, BankBalanceStatus.CURRENCY_MISMATCH, "balance_currency_mismatch"
            )
        if (
            balance.freshness is not FreshnessStatus.FRESH
            or balance.observed_at < request.fresh_after
        ):
            return _outcome(request, BankBalanceStatus.STALE, "balance_stale")
        return BankBalanceOutcome(
            request=request, status=BankBalanceStatus.AVAILABLE, balance=balance
        )


def _matches_request(balance: BankBalance, request: BankBalanceRequest) -> bool:
    return (
        balance.source == request.source
        and balance.account_reference == request.account_reference
        and balance.correlation_id == request.correlation_id
    )


def _outcome(
    request: BankBalanceRequest,
    status: BankBalanceStatus,
    reason_code: Identifier | None,
) -> BankBalanceOutcome:
    assert status is not BankBalanceStatus.AVAILABLE
    assert reason_code is not None
    return BankBalanceOutcome(request=request, status=status, reason_code=reason_code)
