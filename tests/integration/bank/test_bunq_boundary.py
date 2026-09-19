from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.test import TestCase

from qbet.bank.balances import BankBalanceRequest, ReadOnlyBankBalanceService
from qbet.bank.bunq import (
    BunqAccountSnapshot,
    BunqBalanceProvider,
    BunqOperatingMode,
    BunqSandboxFundingAdapter,
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
from qbet.storage.funding import BunqSandboxSimulationFundingService
from qbet.storage.ledger import PortfolioLedgerRepository
from qbet.storage.simulation_ledger import SimulationPortfolioLedgerRepository

NOW = datetime.now(UTC).replace(microsecond=0)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
PROPOSAL_ID = UUID("87654321-4321-8765-4321-876543218765")
SOURCE = DataSourceMetadata(provider_id="bunq", source_id="bunq_sandbox", transport=SourceTransport.API)


class SandboxTransport:
    mode = BunqOperatingMode.SANDBOX

    def __init__(self) -> None:
        self.payment_calls = 0

    def read_account(self) -> BunqAccountSnapshot:
        return BunqAccountSnapshot(
            currency="EUR",
            available_balance=Decimal("100"),
            current_balance=Decimal("100"),
            observed_at=NOW,
        )

    def create_sandbox_payment(
        self,
        *,
        amount: Decimal,
        currency: str,
        recipient_email: str,
        test_reference: str,
    ) -> str:
        assert amount == Decimal("0.01")
        assert currency == "EUR"
        assert recipient_email == "sandbox@example.invalid"
        assert test_reference == f"qbet-sandbox-{PROPOSAL_ID}"
        self.payment_calls += 1
        return "bunq-payment-0123456789ab"


def test_approved_funding_path_reaches_bunq_sandbox_without_mutating_ledger() -> None:
    transport = SandboxTransport()
    balance_reader = ReadOnlyBankBalanceService(
        BunqBalanceProvider(
            source=SOURCE,
            account_reference="bunq-***1234",
            transport=transport,
        )
    )
    balance_outcome = balance_reader.read_balance(
        BankBalanceRequest(
            source=SOURCE,
            account_reference="bunq-***1234",
            currency="EUR",
            correlation_id=CORRELATION_ID,
            fresh_after=NOW - timedelta(minutes=1),
        )
    )
    balance = balance_outcome.require_fresh_balance()

    ledger = PortfolioLedger(
        balance=PortfolioBalance(
            mode="execution",
            currency="EUR",
            available=Decimal("100"),
        )
    )
    original_ledger = ledger

    proposal = BankFundingProposal(
        id=PROPOSAL_ID,
        direction=FundingDirection.FUNDING,
        source_role=FundingAccountRole.BANK_ACCOUNT,
        destination_role=FundingAccountRole.PORTFOLIO_LEDGER,
        amount=Decimal("0.01"),
        currency="EUR",
        reason="bunq_sandbox_e2e",
        target_mode="execution",
        target_context="owner:execution",
        correlation_id=CORRELATION_ID,
        created_at=NOW - timedelta(minutes=2),
        expires_at=NOW + timedelta(minutes=3),
        lifecycle_at=NOW - timedelta(minutes=2),
    )
    service = BankFundingProposalService(
        max_amount=Decimal("1"),
        max_balance_age=timedelta(minutes=2),
    )
    awaiting = service.request_approval(
        proposal,
        requested_at=NOW - timedelta(minutes=1),
    )
    assert awaiting.accepted

    approved = service.approve(
        awaiting.proposal,
        approver=FundingApprover(identity="staff-1", is_authenticated=True),
        approved_at=NOW,
        balance=balance,
        ledger=ledger,
        capital_context="owner:execution",
    )
    assert approved.accepted

    result = BunqSandboxFundingAdapter(
        transport=transport,
        recipient_email="sandbox@example.invalid",
    ).execute(approved.proposal)

    assert result.sent
    assert result.correlation_id == CORRELATION_ID
    assert transport.payment_calls == 1
    assert ledger == original_ledger


class BunqSandboxSimulationFundingBoundaryTests(TestCase):
    def test_approved_simulation_funding_reaches_sandbox_and_credits_ledger_once(self) -> None:
        transport = SandboxTransport()
        balance = ReadOnlyBankBalanceService(
            BunqBalanceProvider(
                source=SOURCE,
                account_reference="bunq-***1234",
                transport=transport,
            )
        ).read_balance(
            BankBalanceRequest(
                source=SOURCE,
                account_reference="bunq-***1234",
                currency="EUR",
                correlation_id=CORRELATION_ID,
                fresh_after=NOW - timedelta(minutes=1),
            )
        ).require_fresh_balance()
        ledger = SimulationPortfolioLedgerRepository().load_or_create(
            PortfolioLedger(
                balance=PortfolioBalance(
                    mode="simulation",
                    currency="EUR",
                    available=Decimal("10"),
                )
            )
        )
        proposal = BankFundingProposal(
            id=PROPOSAL_ID,
            direction=FundingDirection.FUNDING,
            source_role=FundingAccountRole.BANK_ACCOUNT,
            destination_role=FundingAccountRole.PORTFOLIO_LEDGER,
            amount=Decimal("0.01"),
            currency="EUR",
            reason="bunq_sandbox_simulation_funding",
            target_mode="simulation",
            target_context="owner:simulation",
            correlation_id=CORRELATION_ID,
            created_at=NOW - timedelta(minutes=2),
            expires_at=NOW + timedelta(minutes=3),
            lifecycle_at=NOW - timedelta(minutes=2),
        )
        service = BankFundingProposalService(
            max_amount=Decimal("1"),
            max_balance_age=timedelta(minutes=2),
        )
        awaiting = service.request_approval(
            proposal,
            requested_at=NOW - timedelta(minutes=1),
        )
        assert awaiting.accepted
        approved = service.approve(
            awaiting.proposal,
            approver=FundingApprover(identity="staff-1", is_authenticated=True),
            approved_at=NOW,
            balance=balance,
            ledger=ledger,
            capital_context="owner:simulation",
        )
        assert approved.accepted

        funding_service = BunqSandboxSimulationFundingService(
            transport=transport,
            recipient_email="sandbox@example.invalid",
        )
        first = funding_service.execute(approved.proposal)
        replay = funding_service.execute(approved.proposal)
        persisted = PortfolioLedgerRepository().load(
            mode="simulation",
            currency="EUR",
        )

        assert first.provider_result.sent
        assert first.ledger_applied
        assert replay.duplicate
        assert transport.payment_calls == 1
        assert persisted is not None
        assert persisted.balance.available == Decimal("10.01")
