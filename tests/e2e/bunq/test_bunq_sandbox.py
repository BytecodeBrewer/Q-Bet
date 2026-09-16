from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
from uuid import uuid4

import pytest

from qbet.bank.balances import BankBalanceRequest, ReadOnlyBankBalanceService
from qbet.bank.bunq import (
    BunqBalanceProvider,
    BunqOperatingMode,
    BunqSandboxFundingAdapter,
    BunqSdkTransport,
    BunqSettings,
)
from qbet.bank.funding import (
    BankFundingProposal,
    BankFundingProposalService,
    FundingAccountRole,
    FundingApprover,
    FundingDirection,
)
from qbet.data.models import DataSourceMetadata, SourceTransport
from qbet.domain.ledger import PortfolioBalance
from qbet.ledger import PortfolioLedger

pytestmark = pytest.mark.bunq_e2e

if os.getenv("QBET_BUNQ_E2E") != "1":
    pytest.skip(
        "bunq sandbox E2E is opt-in; set QBET_BUNQ_E2E=1 in the protected workflow",
        allow_module_level=True,
    )


def test_bunq_official_sandbox_balance_and_approved_payment() -> None:
    settings = BunqSettings.from_environment()
    if settings.mode is not BunqOperatingMode.SANDBOX:
        pytest.fail("bunq E2E refuses to run unless QBET_BUNQ_MODE=sandbox")
    if settings.sandbox_recipient_email is None:
        pytest.fail("QBET_BUNQ_SANDBOX_RECIPIENT_EMAIL is required for bunq E2E")

    read_started_at = datetime.now(UTC)
    correlation_id = uuid4()
    source = DataSourceMetadata(
        provider_id="bunq",
        source_id="bunq_sandbox",
        transport=SourceTransport.API,
    )
    transport = BunqSdkTransport(settings)
    reader = ReadOnlyBankBalanceService(
        BunqBalanceProvider(
            source=source,
            account_reference=settings.account_reference,
            transport=transport,
        )
    )
    balance_outcome = reader.read_balance(
        BankBalanceRequest(
            source=source,
            account_reference=settings.account_reference,
            currency="EUR",
            correlation_id=correlation_id,
            fresh_after=read_started_at - timedelta(minutes=5),
        )
    )
    balance = balance_outcome.require_fresh_balance()
    amount = Decimal("0.01")
    if balance.available_balance < amount:
        pytest.skip("configured bunq sandbox account has less than EUR 0.01 available")

    lifecycle_now = datetime.now(UTC)
    ledger = PortfolioLedger(
        balance=PortfolioBalance(
            mode="execution",
            currency="EUR",
            available=Decimal("1.00"),
        )
    )
    proposal = BankFundingProposal(
        id=uuid4(),
        direction=FundingDirection.FUNDING,
        source_role=FundingAccountRole.BANK_ACCOUNT,
        destination_role=FundingAccountRole.PORTFOLIO_LEDGER,
        amount=amount,
        currency="EUR",
        reason="bunq_sandbox_e2e",
        target_mode="execution",
        target_context="owner:execution",
        correlation_id=correlation_id,
        created_at=lifecycle_now - timedelta(seconds=10),
        expires_at=lifecycle_now + timedelta(minutes=5),
        lifecycle_at=lifecycle_now - timedelta(seconds=10),
    )
    proposal_service = BankFundingProposalService(
        max_amount=Decimal("0.01"),
        max_balance_age=timedelta(minutes=5),
    )
    awaiting = proposal_service.request_approval(
        proposal,
        requested_at=lifecycle_now - timedelta(seconds=5),
    )
    assert awaiting.accepted
    approved = proposal_service.approve(
        awaiting.proposal,
        approver=FundingApprover(identity="bunq-e2e", is_authenticated=True),
        approved_at=lifecycle_now,
        balance=balance,
        ledger=ledger,
        capital_context="owner:execution",
    )
    assert approved.accepted

    adapter = BunqSandboxFundingAdapter(
        transport=transport,
        recipient_email=settings.sandbox_recipient_email,
    )
    first = adapter.execute(approved.proposal)
    replay = adapter.execute(approved.proposal)

    assert first.sent
    assert first.correlation_id == correlation_id
    assert first.provider_reference is not None
    assert replay.sent
    assert replay.duplicate
    assert replay.provider_reference == first.provider_reference
