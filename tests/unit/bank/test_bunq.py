from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from qbet.bank.balances import BankBalanceRequest, BankBalanceStatus, ReadOnlyBankBalanceService
from qbet.bank.bunq import (
    BunqAccountSnapshot,
    BunqBalanceProvider,
    BunqConfigurationError,
    BunqOperatingMode,
    BunqSandboxFundingAdapter,
    BunqSdkTransport,
    BunqSettings,
    BunqTransportError,
    _sdk_reason_code,
)
from qbet.bank.funding import (
    BankFundingProposal,
    FundingAccountRole,
    FundingApproval,
    FundingApprover,
    FundingDirection,
    FundingProposalState,
)
from qbet.data.models import DataSourceMetadata, SourceTransport

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
PROPOSAL_ID = UUID("87654321-4321-8765-4321-876543218765")
SOURCE = DataSourceMetadata(provider_id="bunq", source_id="bunq_test", transport=SourceTransport.API)


class FakeBunqTransport:
    def __init__(
        self,
        *,
        mode: BunqOperatingMode = BunqOperatingMode.SANDBOX,
        snapshot: BunqAccountSnapshot | None = None,
        error: BunqTransportError | None = None,
        payment_error: BunqTransportError | None = None,
    ) -> None:
        self._mode = mode
        self.snapshot = snapshot or BunqAccountSnapshot(
            currency="EUR",
            available_balance=Decimal("100"),
            current_balance=Decimal("100"),
            observed_at=NOW,
        )
        self.error = error
        self.payment_error = payment_error
        self.balance_calls = 0
        self.payment_calls = 0
        self.last_test_reference: str | None = None

    @property
    def mode(self) -> BunqOperatingMode:
        return self._mode

    def read_account(self) -> BunqAccountSnapshot:
        self.balance_calls += 1
        if self.error is not None:
            raise self.error
        return self.snapshot

    def create_sandbox_payment(
        self,
        *,
        amount: Decimal,
        currency: str,
        recipient_email: str,
        test_reference: str,
    ) -> str:
        del amount, currency, recipient_email
        self.payment_calls += 1
        self.last_test_reference = test_reference
        if self.payment_error is not None:
            raise self.payment_error
        return "bunq-payment-deadbeef1234"


def balance_request(**changes: object) -> BankBalanceRequest:
    values: dict[str, object] = {
        "source": SOURCE,
        "account_reference": "bunq-***1234",
        "currency": "EUR",
        "correlation_id": CORRELATION_ID,
        "fresh_after": NOW - timedelta(minutes=5),
    }
    values.update(changes)
    return BankBalanceRequest.model_validate(values)


def reader(transport: FakeBunqTransport) -> ReadOnlyBankBalanceService:
    return ReadOnlyBankBalanceService(
        BunqBalanceProvider(
            source=SOURCE,
            account_reference="bunq-***1234",
            transport=transport,
        )
    )


def approved_proposal(**changes: object) -> BankFundingProposal:
    values: dict[str, object] = {
        "id": PROPOSAL_ID,
        "direction": FundingDirection.FUNDING,
        "source_role": FundingAccountRole.BANK_ACCOUNT,
        "destination_role": FundingAccountRole.PORTFOLIO_LEDGER,
        "amount": Decimal("0.01"),
        "currency": "EUR",
        "reason": "bunq_sandbox_e2e",
        "target_mode": "execution",
        "target_context": "owner:execution",
        "correlation_id": CORRELATION_ID,
        "created_at": NOW - timedelta(minutes=2),
        "expires_at": NOW + timedelta(minutes=3),
        "state": FundingProposalState.APPROVED,
        "lifecycle_at": NOW,
        "approval": FundingApproval(
            approver=FundingApprover(identity="staff-1", is_authenticated=True),
            approved_at=NOW,
        ),
    }
    values.update(changes)
    return BankFundingProposal.model_validate(values)


def test_settings_default_to_read_only_and_keep_secrets_out_of_repr() -> None:
    settings = BunqSettings.from_environment(
        {
            "API_KEY_BUNQ": "secret-value",
            "QBET_BUNQ_ACCOUNT_REFERENCE": "bunq-***1234",
        }
    )

    assert settings.mode is BunqOperatingMode.READ_ONLY
    rendered = repr(settings)
    assert "secret-value" not in rendered
    assert "api_key=<redacted>" in rendered


@pytest.mark.parametrize(
    "environ",
    [
        {"QBET_BUNQ_ACCOUNT_REFERENCE": "bunq-***1234"},
        {"API_KEY_BUNQ": "secret-value"},
        {
            "API_KEY_BUNQ": "secret-value",
            "QBET_BUNQ_ACCOUNT_REFERENCE": "bunq-***1234",
            "QBET_BUNQ_MODE": "write_live",
        },
    ],
)
def test_invalid_or_missing_configuration_fails_before_network_use(
    environ: dict[str, str],
) -> None:
    with pytest.raises(BunqConfigurationError):
        BunqSettings.from_environment(environ)


def test_balance_provider_normalizes_success_without_exposing_provider_identity() -> None:
    outcome = reader(FakeBunqTransport()).read_balance(balance_request())

    balance = outcome.require_fresh_balance()
    assert balance.available_balance == Decimal("100")
    assert balance.current_balance == Decimal("100")
    assert balance.account_reference == "bunq-***1234"
    assert balance.correlation_id == CORRELATION_ID


@pytest.mark.parametrize(
    ("snapshot", "status", "reason_code"),
    [
        (
            BunqAccountSnapshot(
                currency="GBP",
                available_balance=Decimal("100"),
                current_balance=Decimal("100"),
                observed_at=NOW,
            ),
            BankBalanceStatus.CURRENCY_MISMATCH,
            "balance_currency_mismatch",
        ),
        (
            BunqAccountSnapshot(
                currency="EUR",
                available_balance=Decimal("100"),
                current_balance=Decimal("100"),
                observed_at=NOW - timedelta(minutes=10),
            ),
            BankBalanceStatus.STALE,
            "balance_stale",
        ),
    ],
)
def test_balance_service_preserves_currency_and_freshness_guards(
    snapshot: BunqAccountSnapshot,
    status: BankBalanceStatus,
    reason_code: str,
) -> None:
    outcome = reader(FakeBunqTransport(snapshot=snapshot)).read_balance(balance_request())

    assert outcome.status is status
    assert outcome.reason_code == reason_code


@pytest.mark.parametrize(
    ("transport_reason", "status"),
    [
        ("bunq_auth_failed", BankBalanceStatus.UNAVAILABLE),
        ("bunq_timeout", BankBalanceStatus.UNAVAILABLE),
        ("bunq_rate_limited", BankBalanceStatus.UNAVAILABLE),
        ("bunq_provider_unavailable", BankBalanceStatus.UNAVAILABLE),
        ("bunq_invalid_payload", BankBalanceStatus.INVALID),
    ],
)
def test_provider_failures_become_stable_balance_outcomes(
    transport_reason: str,
    status: BankBalanceStatus,
) -> None:
    outcome = reader(
        FakeBunqTransport(error=BunqTransportError(transport_reason))
    ).read_balance(balance_request())

    assert outcome.status is status
    assert outcome.reason_code == transport_reason


@pytest.mark.parametrize(
    ("response_code", "expected"),
    [
        (401, "bunq_auth_failed"),
        (403, "bunq_auth_failed"),
        (429, "bunq_rate_limited"),
        (408, "bunq_timeout"),
        (504, "bunq_timeout"),
        (500, "bunq_provider_unavailable"),
    ],
)
def test_sdk_http_errors_are_classified_without_raw_payloads(
    response_code: int,
    expected: str,
) -> None:
    class FakeSdkError(Exception):
        def __init__(self, code: int) -> None:
            self.response_code = code

    assert _sdk_reason_code(FakeSdkError(response_code)) == expected


def test_sdk_timeout_classification_covers_transport_timeouts() -> None:
    assert _sdk_reason_code(TimeoutError()) == "bunq_timeout"


def test_sdk_is_optional_for_normal_offline_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = BunqSettings(
        mode=BunqOperatingMode.READ_ONLY,
        api_key="secret-value",
        account_reference="bunq-***1234",
    )
    transport = BunqSdkTransport(settings)

    def missing_sdk(name: str) -> object:
        del name
        raise ModuleNotFoundError("optional bunq sdk is not installed")

    monkeypatch.setattr("qbet.bank.bunq.import_module", missing_sdk)

    with pytest.raises(BunqTransportError) as error:
        transport.read_account()

    assert error.value.reason_code == "bunq_sdk_unavailable"


def test_live_read_only_mode_technically_blocks_provider_writes() -> None:
    transport = FakeBunqTransport(mode=BunqOperatingMode.READ_ONLY)
    adapter = BunqSandboxFundingAdapter(
        transport=transport,
        recipient_email="sandbox@example.invalid",
    )

    result = adapter.execute(approved_proposal())

    assert not result.sent
    assert result.reason_code == "bunq_write_forbidden"
    assert transport.payment_calls == 0


@pytest.mark.parametrize(
    ("proposal_changes", "reason_code"),
    [
        (
            {
                "state": FundingProposalState.AWAITING_APPROVAL,
                "approval": None,
                "lifecycle_at": NOW - timedelta(minutes=1),
            },
            "proposal_not_approved",
        ),
        (
            {
                "target_mode": "simulation",
                "target_context": "owner:simulation",
            },
            "bunq_sandbox_payment_requires_execution_context",
        ),
        (
            {
                "direction": FundingDirection.WITHDRAWAL,
                "source_role": FundingAccountRole.PORTFOLIO_LEDGER,
                "destination_role": FundingAccountRole.BANK_ACCOUNT,
            },
            "bunq_sandbox_payment_direction_unsupported",
        ),
    ],
)
def test_sandbox_payment_requires_approved_execution_funding_boundary(
    proposal_changes: dict[str, object],
    reason_code: str,
) -> None:
    transport = FakeBunqTransport()
    adapter = BunqSandboxFundingAdapter(
        transport=transport,
        recipient_email="sandbox@example.invalid",
    )

    result = adapter.execute(approved_proposal(**proposal_changes))

    assert not result.sent
    assert result.reason_code == reason_code
    assert transport.payment_calls == 0


def test_sandbox_payment_is_correlated_and_idempotent() -> None:
    transport = FakeBunqTransport()
    adapter = BunqSandboxFundingAdapter(
        transport=transport,
        recipient_email="sandbox@example.invalid",
    )
    proposal = approved_proposal()

    first = adapter.execute(proposal)
    repeated = adapter.execute(proposal)

    assert first.sent
    assert first.correlation_id == CORRELATION_ID
    assert first.test_reference == f"qbet-sandbox-{PROPOSAL_ID}"
    assert first.provider_reference == "bunq-payment-deadbeef1234"
    assert repeated.sent
    assert repeated.duplicate
    assert transport.payment_calls == 1
    assert transport.last_test_reference == first.test_reference


def test_ambiguous_provider_failure_is_cached_to_prevent_duplicate_retry() -> None:
    transport = FakeBunqTransport(payment_error=BunqTransportError("bunq_timeout"))
    adapter = BunqSandboxFundingAdapter(
        transport=transport,
        recipient_email="sandbox@example.invalid",
    )
    proposal = approved_proposal()

    first = adapter.execute(proposal)
    repeated = adapter.execute(proposal)

    assert not first.sent
    assert first.reason_code == "bunq_timeout"
    assert repeated.duplicate
    assert transport.payment_calls == 1


def test_sandbox_payment_requires_explicit_recipient_configuration() -> None:
    transport = FakeBunqTransport()
    adapter = BunqSandboxFundingAdapter(transport=transport, recipient_email=None)

    result = adapter.execute(approved_proposal())

    assert not result.sent
    assert result.reason_code == "bunq_sandbox_recipient_missing"
    assert transport.payment_calls == 0
