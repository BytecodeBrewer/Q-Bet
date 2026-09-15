"""bunq bank adapter with strict read-only and official sandbox execution modes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from importlib import import_module
import os
import re
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, field_validator, model_validator

from qbet.bank.balances import (
    BankBalance,
    BankBalanceProviderResponse,
    BankBalanceRequest,
    BankBalanceStatus,
)
from qbet.bank.funding import BankFundingProposal, FundingDirection, FundingProposalState
from qbet.data.models import DataSourceMetadata, FreshnessStatus
from qbet.domain.models import Currency, DomainModel, Identifier, NonNegativeDecimal


_ACCOUNT_REFERENCE = re.compile(r"^[a-z][a-z_-]{0,23}-\*{3}[0-9]{4}$", re.IGNORECASE)


class BunqOperatingMode(StrEnum):
    """Explicit bunq authority boundary."""

    READ_ONLY = "read_only"
    SANDBOX = "sandbox"


class BunqConfigurationError(ValueError):
    """Safe configuration error that never includes credential values."""


class BunqTransportError(RuntimeError):
    """Stable provider failure without raw provider payloads or credentials."""

    def __init__(self, reason_code: Identifier) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True, repr=False)
class BunqSettings:
    """Runtime-only bunq configuration.

    The API key is deliberately never rendered by repr and is never persisted by this adapter.
    """

    mode: BunqOperatingMode
    api_key: str
    account_reference: str
    sandbox_recipient_email: str | None = None
    device_description: str = "Q-Bet bunq adapter"

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise BunqConfigurationError("API_KEY_BUNQ is required")
        if not self.account_reference.strip():
            raise BunqConfigurationError("QBET_BUNQ_ACCOUNT_REFERENCE is required")
        if not _ACCOUNT_REFERENCE.fullmatch(self.account_reference.strip()):
            raise BunqConfigurationError(
                "QBET_BUNQ_ACCOUNT_REFERENCE must use the opaque-label-***1234 format"
            )
        if not self.device_description.strip():
            raise BunqConfigurationError("QBET_BUNQ_DEVICE_DESCRIPTION cannot be empty")

    def __repr__(self) -> str:
        recipient = "<configured>" if self.sandbox_recipient_email else "<unset>"
        return (
            "BunqSettings("
            f"mode={self.mode!r}, api_key=<redacted>, "
            f"account_reference={self.account_reference!r}, "
            f"sandbox_recipient_email={recipient}, "
            f"device_description={self.device_description!r})"
        )

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> BunqSettings:
        values = os.environ if environ is None else environ
        raw_mode = values.get("QBET_BUNQ_MODE", BunqOperatingMode.READ_ONLY.value).strip()
        try:
            mode = BunqOperatingMode(raw_mode)
        except ValueError as exc:
            raise BunqConfigurationError(
                "QBET_BUNQ_MODE must be read_only or sandbox"
            ) from exc

        api_key = values.get("API_KEY_BUNQ", "")
        account_reference = values.get("QBET_BUNQ_ACCOUNT_REFERENCE", "")
        recipient = values.get("QBET_BUNQ_SANDBOX_RECIPIENT_EMAIL", "").strip() or None
        device_description = (
            values.get("QBET_BUNQ_DEVICE_DESCRIPTION", "Q-Bet bunq adapter").strip()
        )
        return cls(
            mode=mode,
            api_key=api_key,
            account_reference=account_reference,
            sandbox_recipient_email=recipient,
            device_description=device_description,
        )


class BunqAccountSnapshot(DomainModel):
    """Provider observation before Q-Bet request identity validation."""

    currency: Currency
    available_balance: NonNegativeDecimal
    current_balance: NonNegativeDecimal
    observed_at: AwareDatetime

    @field_validator("available_balance", "current_balance", mode="before")
    @classmethod
    def balance_is_not_binary_float(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("bunq balance amounts must not use binary floating point")
        return value


class BunqTransport(Protocol):
    """Narrow transport seam so provider behavior remains mockable offline."""

    @property
    def mode(self) -> BunqOperatingMode: ...

    def read_account(self) -> BunqAccountSnapshot: ...

    def create_sandbox_payment(
        self,
        *,
        amount: Decimal,
        currency: Currency,
        recipient_email: str,
        test_reference: Identifier,
    ) -> Identifier: ...


class BunqSdkTransport:
    """Official bunq Python SDK bridge.

    Imports are lazy so normal Q-Bet CI remains offline and does not require the optional SDK.
    """

    def __init__(self, settings: BunqSettings) -> None:
        self._settings = settings
        self._context_loaded = False

    @property
    def mode(self) -> BunqOperatingMode:
        return self._settings.mode

    def read_account(self) -> BunqAccountSnapshot:
        self._ensure_context()
        try:
            bunq_context = import_module("bunq.sdk.context.bunq_context").BunqContext
            endpoint = import_module("bunq.sdk.model.generated.endpoint")
            primary = bunq_context.user_context().primary_monetary_account
            account = endpoint.MonetaryAccountBankApiObject.get(primary.id_).value
            balance = account.balance
            raw_value = balance.value
            raw_currency = balance.currency
            if isinstance(raw_value, float):
                raise BunqTransportError("bunq_invalid_payload")
            try:
                amount = Decimal(str(raw_value))
            except (InvalidOperation, ValueError) as exc:
                raise BunqTransportError("bunq_invalid_payload") from exc
            return BunqAccountSnapshot.model_validate(
                {
                    "currency": str(raw_currency),
                    "available_balance": amount,
                    "current_balance": amount,
                    "observed_at": datetime.now(UTC),
                }
            )
        except BunqTransportError:
            raise
        except Exception as exc:
            raise BunqTransportError(_sdk_reason_code(exc)) from exc

    def create_sandbox_payment(
        self,
        *,
        amount: Decimal,
        currency: Currency,
        recipient_email: str,
        test_reference: Identifier,
    ) -> Identifier:
        if self.mode is not BunqOperatingMode.SANDBOX:
            raise BunqTransportError("bunq_write_forbidden")
        if not recipient_email.strip():
            raise BunqTransportError("bunq_sandbox_recipient_missing")

        self._ensure_context()
        try:
            bunq_context = import_module("bunq.sdk.context.bunq_context").BunqContext
            endpoint = import_module("bunq.sdk.model.generated.endpoint")
            generated = import_module("bunq.sdk.model.generated.object_")
            primary = bunq_context.user_context().primary_monetary_account
            response = endpoint.PaymentApiObject.create(
                generated.AmountObject(format(amount, "f"), currency),
                generated.PointerObject("EMAIL", recipient_email),
                f"Q-Bet sandbox {test_reference}",
                primary.id_,
            )
            provider_id = getattr(response, "value", None)
            if provider_id is None:
                raise BunqTransportError("bunq_invalid_payload")
            return _redacted_provider_reference(provider_id)
        except BunqTransportError:
            raise
        except Exception as exc:
            raise BunqTransportError(_sdk_reason_code(exc)) from exc

    def _ensure_context(self) -> None:
        if self._context_loaded:
            return
        try:
            api_context_module = import_module("bunq.sdk.context.api_context")
            environment_module = import_module("bunq.sdk.context.api_environment_type")
            bunq_context_module = import_module("bunq.sdk.context.bunq_context")
            environment = (
                environment_module.ApiEnvironmentType.SANDBOX
                if self.mode is BunqOperatingMode.SANDBOX
                else environment_module.ApiEnvironmentType.PRODUCTION
            )
            context = api_context_module.ApiContext.create(
                environment,
                self._settings.api_key,
                self._settings.device_description,
            )
            bunq_context_module.BunqContext.load_api_context(context)
            self._context_loaded = True
        except ModuleNotFoundError as exc:
            raise BunqTransportError("bunq_sdk_unavailable") from exc
        except Exception as exc:
            raise BunqTransportError(_sdk_reason_code(exc)) from exc


class BunqBalanceProvider:
    """Map bunq account observations into Q-Bet's existing read-only balance contract."""

    def __init__(
        self,
        *,
        source: DataSourceMetadata,
        account_reference: Identifier,
        transport: BunqTransport,
    ) -> None:
        self._source = source
        self._account_reference = account_reference
        self._transport = transport

    def read_balance(self, request: BankBalanceRequest) -> BankBalanceProviderResponse:
        try:
            snapshot = self._transport.read_account()
            balance = BankBalance(
                source=self._source,
                account_reference=self._account_reference,
                currency=snapshot.currency,
                available_balance=snapshot.available_balance,
                current_balance=snapshot.current_balance,
                observed_at=snapshot.observed_at,
                freshness=FreshnessStatus.FRESH,
                correlation_id=request.correlation_id,
            )
        except BunqTransportError as exc:
            status = (
                BankBalanceStatus.INVALID
                if exc.reason_code == "bunq_invalid_payload"
                else BankBalanceStatus.UNAVAILABLE
            )
            return BankBalanceProviderResponse(status=status, reason_code=exc.reason_code)
        except ValueError:
            return BankBalanceProviderResponse(
                status=BankBalanceStatus.INVALID,
                reason_code="bunq_invalid_payload",
            )

        return BankBalanceProviderResponse(
            status=BankBalanceStatus.AVAILABLE,
            balance=balance,
        )


class BunqSandboxPaymentResult(DomainModel):
    """Correlated sandbox-only provider outcome; it never mutates PortfolioLedger."""

    proposal_id: UUID
    correlation_id: UUID
    test_reference: Identifier
    sent: bool
    duplicate: bool = False
    provider_reference: Identifier | None = None
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_result(self) -> BunqSandboxPaymentResult:
        if self.sent:
            if self.provider_reference is None or self.reason_code is not None:
                raise ValueError("sent sandbox payments require a provider reference only")
        elif self.provider_reference is not None or self.reason_code is None:
            raise ValueError("failed sandbox payments require a reason code only")
        return self


class BunqSandboxFundingAdapter:
    """Execute an already-approved funding proposal only against bunq's fake-money sandbox."""

    def __init__(self, *, transport: BunqTransport, recipient_email: str | None) -> None:
        self._transport = transport
        self._recipient_email = recipient_email
        self._attempts: dict[UUID, tuple[BankFundingProposal, BunqSandboxPaymentResult]] = {}

    def execute(self, proposal: BankFundingProposal) -> BunqSandboxPaymentResult:
        previous = self._attempts.get(proposal.id)
        if previous is not None:
            original, result = previous
            if original != proposal:
                return _failed_result(
                    proposal,
                    "bunq_sandbox_payment_conflict",
                    duplicate=True,
                )
            return result.model_copy(update={"duplicate": True})

        reason = self._guard_reason(proposal)
        if reason is not None:
            result = _failed_result(proposal, reason)
            self._attempts[proposal.id] = (proposal, result)
            return result

        assert self._recipient_email is not None
        reference = _sandbox_test_reference(proposal.id)
        try:
            provider_reference = self._transport.create_sandbox_payment(
                amount=proposal.amount,
                currency=proposal.currency,
                recipient_email=self._recipient_email,
                test_reference=reference,
            )
            result = BunqSandboxPaymentResult(
                proposal_id=proposal.id,
                correlation_id=proposal.correlation_id,
                test_reference=reference,
                sent=True,
                provider_reference=provider_reference,
            )
        except BunqTransportError as exc:
            result = _failed_result(proposal, exc.reason_code, test_reference=reference)

        self._attempts[proposal.id] = (proposal, result)
        return result

    def _guard_reason(self, proposal: BankFundingProposal) -> Identifier | None:
        if self._transport.mode is not BunqOperatingMode.SANDBOX:
            return "bunq_write_forbidden"
        if proposal.state is not FundingProposalState.APPROVED:
            return "proposal_not_approved"
        if datetime.now(UTC) >= proposal.expires_at:
            return "proposal_expired"
        if proposal.target_mode != "execution":
            return "bunq_sandbox_payment_requires_execution_context"
        if proposal.direction is not FundingDirection.FUNDING:
            return "bunq_sandbox_payment_direction_unsupported"
        if not self._recipient_email:
            return "bunq_sandbox_recipient_missing"
        return None


def _failed_result(
    proposal: BankFundingProposal,
    reason_code: Identifier,
    *,
    test_reference: Identifier | None = None,
    duplicate: bool = False,
) -> BunqSandboxPaymentResult:
    return BunqSandboxPaymentResult(
        proposal_id=proposal.id,
        correlation_id=proposal.correlation_id,
        test_reference=test_reference or _sandbox_test_reference(proposal.id),
        sent=False,
        duplicate=duplicate,
        reason_code=reason_code,
    )


def _sandbox_test_reference(proposal_id: UUID) -> Identifier:
    return f"qbet-sandbox-{proposal_id}"


def _redacted_provider_reference(provider_id: object) -> Identifier:
    digest = sha256(str(provider_id).encode("utf-8")).hexdigest()[:12]
    return f"bunq-payment-{digest}"


def _sdk_reason_code(exc: Exception) -> Identifier:
    response_code = getattr(exc, "response_code", None)
    if response_code in {401, 403}:
        return "bunq_auth_failed"
    if response_code == 429:
        return "bunq_rate_limited"
    if response_code in {408, 504}:
        return "bunq_timeout"

    exception_name = type(exc).__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in exception_name:
        return "bunq_timeout"
    return "bunq_provider_unavailable"
