from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from qbet.bank import (
    BankBalance,
    BankBalanceFixture,
    BankBalanceProvider,
    BankBalanceProviderResponse,
    BankBalanceRequest,
    BankBalanceStatus,
    DeterministicBankBalanceProvider,
    ReadOnlyBankBalanceService,
)
from qbet.data import DataSourceMetadata, FreshnessStatus, SourceTransport

SOURCE = DataSourceMetadata(
    provider_id="bank_sandbox", source_id="fixture_bank", transport=SourceTransport.IN_MEMORY
)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
OBSERVED_AT = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def request(**changes: object) -> BankBalanceRequest:
    values: dict[str, object] = {
        "source": SOURCE,
        "account_reference": "account-***1234",
        "currency": "EUR",
        "correlation_id": CORRELATION_ID,
        "fresh_after": datetime(2026, 9, 12, 11, 0, tzinfo=UTC),
    }
    values.update(changes)
    return BankBalanceRequest.model_validate(values)


def balance(**changes: object) -> BankBalance:
    values: dict[str, object] = {
        "source": SOURCE,
        "account_reference": "account-***1234",
        "currency": "EUR",
        "available_balance": Decimal("123.45"),
        "current_balance": Decimal("130.00"),
        "observed_at": OBSERVED_AT,
        "freshness": FreshnessStatus.FRESH,
        "correlation_id": CORRELATION_ID,
    }
    values.update(changes)
    return BankBalance.model_validate(values)


def service(
    response: BankBalanceProviderResponse | dict[str, object],
) -> ReadOnlyBankBalanceService:
    provider = DeterministicBankBalanceProvider(
        (BankBalanceFixture(source=SOURCE, account_reference="account-***1234", response=response),)
    )
    assert isinstance(provider, BankBalanceProvider)
    return ReadOnlyBankBalanceService(provider)


def available_response(value: BankBalance | None = None) -> BankBalanceProviderResponse:
    return BankBalanceProviderResponse(
        status=BankBalanceStatus.AVAILABLE, balance=value or balance()
    )


def test_available_balance_preserves_decimal_identity_and_correlation() -> None:
    outcome = service(available_response()).read_balance(request())

    normalized = outcome.require_fresh_balance()
    assert outcome.status is BankBalanceStatus.AVAILABLE
    assert normalized.available_balance == Decimal("123.45")
    assert normalized.current_balance == Decimal("130.00")
    assert normalized.source == SOURCE
    assert normalized.correlation_id == CORRELATION_ID


def test_exact_freshness_boundary_is_accepted() -> None:
    boundary = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)

    outcome = service(available_response(balance(observed_at=boundary))).read_balance(
        request(fresh_after=boundary)
    )

    assert outcome.status is BankBalanceStatus.AVAILABLE


@pytest.mark.parametrize(
    ("response", "status", "reason_code"),
    [
        (
            BankBalanceProviderResponse(
                status=BankBalanceStatus.UNAVAILABLE, reason_code="provider_unavailable"
            ),
            BankBalanceStatus.UNAVAILABLE,
            "provider_unavailable",
        ),
        (
            available_response(balance(observed_at=datetime(2026, 9, 12, 10, 0, tzinfo=UTC))),
            BankBalanceStatus.STALE,
            "balance_stale",
        ),
        (
            available_response(balance(freshness=FreshnessStatus.STALE)),
            BankBalanceStatus.STALE,
            "balance_stale",
        ),
        ({"unexpected": "payload"}, BankBalanceStatus.INVALID, "balance_response_invalid"),
    ],
)
def test_unavailable_stale_and_malformed_balances_are_rejected(
    response: BankBalanceProviderResponse | dict[str, object],
    status: BankBalanceStatus,
    reason_code: str,
) -> None:
    outcome = service(response).read_balance(request())

    assert outcome.status is status
    assert outcome.reason_code == reason_code
    with pytest.raises(ValueError, match="not available"):
        outcome.require_fresh_balance()


@pytest.mark.parametrize(
    ("changes", "status", "reason_code"),
    [
        ({"currency": "GBP"}, BankBalanceStatus.CURRENCY_MISMATCH, "balance_currency_mismatch"),
        (
            {
                "source": DataSourceMetadata(
                    provider_id="other", source_id="other", transport=SourceTransport.API
                )
            },
            BankBalanceStatus.MISMATCHED,
            "balance_identity_mismatch",
        ),
        (
            {"correlation_id": UUID("87654321-4321-8765-4321-876543218765")},
            BankBalanceStatus.MISMATCHED,
            "balance_identity_mismatch",
        ),
    ],
)
def test_currency_and_identity_mismatches_are_rejected(
    changes: dict[str, object], status: BankBalanceStatus, reason_code: str
) -> None:
    outcome = service(available_response(balance(**changes))).read_balance(request())

    assert outcome.status is status
    assert outcome.reason_code == reason_code


@pytest.mark.parametrize("amount", [Decimal("-0.01"), Decimal("Infinity"), Decimal("NaN")])
def test_invalid_balance_amounts_are_rejected(amount: Decimal) -> None:
    with pytest.raises(ValueError):
        balance(available_balance=amount)


def test_account_references_must_be_redacted() -> None:
    with pytest.raises(ValueError, match="redacted"):
        request(account_reference="DE89370400440532013000")


def test_missing_fixture_is_unavailable_and_fixture_replay_is_deterministic() -> None:
    provider = DeterministicBankBalanceProvider()
    reader = ReadOnlyBankBalanceService(provider)

    assert reader.read_balance(request()) == reader.read_balance(request())
    assert reader.read_balance(request()).status is BankBalanceStatus.UNAVAILABLE


def test_boundary_exposes_no_capital_mutation_operation() -> None:
    reader = service(available_response())

    assert not hasattr(reader, "transfer")
    assert not hasattr(reader, "reserve")
    assert not hasattr(reader, "write_balance")
