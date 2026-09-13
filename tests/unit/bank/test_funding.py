from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from qbet.bank import (
    BankFundingProposal,
    BankFundingProposalService,
    DeterministicFundingSandboxAdapter,
    FundingAccountRole,
    FundingApprover,
    FundingDirection,
    FundingProposalState,
)
from qbet.data import DataSourceMetadata, FreshnessStatus, SourceTransport
from qbet.ledger import PortfolioLedger
from qbet.domain.ledger import PortfolioBalance
from qbet.bank import BankBalance

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
PROPOSAL_ID = UUID("87654321-4321-8765-4321-876543218765")
SOURCE = DataSourceMetadata(
    provider_id="bank_sandbox", source_id="fixture_bank", transport=SourceTransport.IN_MEMORY
)


def proposal(**changes: object) -> BankFundingProposal:
    values: dict[str, object] = {
        "id": PROPOSAL_ID,
        "direction": FundingDirection.FUNDING,
        "source_role": FundingAccountRole.BANK_ACCOUNT,
        "destination_role": FundingAccountRole.PORTFOLIO_LEDGER,
        "amount": Decimal("25"),
        "currency": "EUR",
        "reason": "capital_rebalance",
        "target_mode": "execution",
        "target_context": "owner:execution",
        "correlation_id": CORRELATION_ID,
        "created_at": NOW - timedelta(minutes=2),
        "expires_at": NOW + timedelta(minutes=3),
        "lifecycle_at": NOW - timedelta(minutes=2),
    }
    values.update(changes)
    return BankFundingProposal.model_validate(values)


def balance(**changes: object) -> BankBalance:
    values: dict[str, object] = {
        "source": SOURCE,
        "account_reference": "account-***1234",
        "currency": "EUR",
        "available_balance": Decimal("100"),
        "observed_at": NOW - timedelta(minutes=1),
        "freshness": FreshnessStatus.FRESH,
        "correlation_id": CORRELATION_ID,
    }
    values.update(changes)
    return BankBalance.model_validate(values)


def ledger(**changes: object) -> PortfolioLedger:
    balance_values: dict[str, object] = {
        "mode": "execution",
        "currency": "EUR",
        "available": Decimal("100"),
    }
    balance_values.update(changes)
    return PortfolioLedger(balance=PortfolioBalance.model_validate(balance_values))


def service() -> BankFundingProposalService:
    return BankFundingProposalService(
        max_amount=Decimal("50"), max_balance_age=timedelta(minutes=2)
    )


def awaiting_approval(**changes: object) -> BankFundingProposal:
    submitted = service().request_approval(
        proposal(**changes), requested_at=NOW - timedelta(minutes=1)
    )
    assert submitted.accepted
    return submitted.proposal


def test_valid_funding_approval_preserves_the_ledger() -> None:
    original_ledger = ledger()
    outcome = service().approve(
        awaiting_approval(),
        approver=FundingApprover(identity="staff-1", is_authenticated=True),
        approved_at=NOW,
        balance=balance(),
        ledger=original_ledger,
    )

    assert outcome.accepted
    assert outcome.proposal.state is FundingProposalState.APPROVED
    assert outcome.proposal.approval is not None
    assert original_ledger == ledger()


@pytest.mark.parametrize(
    ("approver", "changes", "reason"),
    [
        (
            FundingApprover(identity="staff-1", is_authenticated=False),
            {},
            "approver_not_authenticated",
        ),
        (
            FundingApprover(identity="staff-1", is_authenticated=True),
            {"amount": Decimal("51")},
            "amount_exceeds_limit",
        ),
        (
            FundingApprover(identity="staff-1", is_authenticated=True),
            {"expires_at": NOW},
            "proposal_expired",
        ),
    ],
)
def test_approval_rejects_untrusted_over_limit_and_expired_proposals(
    approver: FundingApprover, changes: dict[str, object], reason: str
) -> None:
    item = awaiting_approval(**changes)
    outcome = service().approve(
        item, approver=approver, approved_at=NOW, balance=balance(), ledger=ledger()
    )

    assert not outcome.accepted
    assert outcome.reason_code == reason


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("Infinity"), Decimal("NaN"), 25.0])
def test_proposals_reject_non_positive_non_finite_and_float_amounts(amount: object) -> None:
    with pytest.raises(ValueError):
        proposal(amount=amount)


def test_proposals_require_roles_that_match_the_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        proposal(
            direction=FundingDirection.FUNDING,
            source_role=FundingAccountRole.PORTFOLIO_LEDGER,
            destination_role=FundingAccountRole.BANK_ACCOUNT,
        )


@pytest.mark.parametrize(
    ("balance_changes", "ledger_changes", "reason"),
    [
        ({"currency": "GBP"}, {}, "currency_mismatch"),
        ({}, {"currency": "GBP"}, "currency_mismatch"),
        ({"freshness": FreshnessStatus.STALE}, {}, "balance_stale"),
        ({"observed_at": NOW - timedelta(minutes=3)}, {}, "balance_stale"),
    ],
)
def test_approval_checks_currency_and_fresh_observed_balance(
    balance_changes: dict[str, object], ledger_changes: dict[str, object], reason: str
) -> None:
    outcome = service().approve(
        awaiting_approval(),
        approver=FundingApprover(identity="staff-1", is_authenticated=True),
        approved_at=NOW,
        balance=balance(**balance_changes),
        ledger=ledger(**ledger_changes),
    )

    assert not outcome.accepted
    assert outcome.reason_code == reason


def test_withdrawal_checks_ledger_capital_without_mutating_it() -> None:
    item = awaiting_approval(
        direction=FundingDirection.WITHDRAWAL,
        source_role=FundingAccountRole.PORTFOLIO_LEDGER,
        destination_role=FundingAccountRole.BANK_ACCOUNT,
        amount=Decimal("30"),
    )
    original_ledger = ledger(available=Decimal("20"))
    outcome = service().approve(
        item,
        approver=FundingApprover(identity="staff-1", is_authenticated=True),
        approved_at=NOW,
        balance=balance(),
        ledger=original_ledger,
    )

    assert not outcome.accepted
    assert outcome.reason_code == "insufficient_available_capital"
    assert original_ledger == ledger(available=Decimal("20"))


def test_approval_is_idempotent_but_conflicts_are_rejected() -> None:
    original = awaiting_approval()
    first = service().approve(
        original,
        approver=FundingApprover(identity="staff-1", is_authenticated=True),
        approved_at=NOW,
        balance=balance(),
        ledger=ledger(),
    )
    repeated = service().approve(
        first.proposal,
        approver=FundingApprover(identity="staff-1", is_authenticated=True),
        approved_at=NOW,
        balance=balance(),
        ledger=ledger(),
    )
    conflict = service().approve(
        first.proposal,
        approver=FundingApprover(identity="staff-2", is_authenticated=True),
        approved_at=NOW,
        balance=balance(),
        ledger=ledger(),
    )

    assert repeated.accepted and repeated.duplicate
    assert not conflict.accepted
    assert conflict.reason_code == "approval_conflict"


def test_reject_cancel_and_expire_are_bounded_lifecycle_transitions() -> None:
    submitted = awaiting_approval()
    rejected = service().reject(submitted, rejected_at=NOW)
    cancelled = service().cancel(proposal(), cancelled_at=NOW)
    expired = service().expire(submitted, observed_at=NOW + timedelta(minutes=3))

    assert rejected.proposal.state is FundingProposalState.REJECTED
    assert cancelled.proposal.state is FundingProposalState.CANCELLED
    assert expired.proposal.state is FundingProposalState.EXPIRED
    assert (
        service()
        .approve(
            rejected.proposal,
            approver=FundingApprover(identity="staff-1", is_authenticated=True),
            approved_at=NOW,
            balance=balance(),
            ledger=ledger(),
        )
        .reason_code
        == "proposal_not_awaiting_approval"
    )


def test_deterministic_sandbox_acknowledges_only_approved_proposals_idempotently() -> None:
    adapter = DeterministicFundingSandboxAdapter()
    assert (
        adapter.acknowledge(awaiting_approval(), acknowledged_at=NOW).reason_code
        == "proposal_not_approved"
    )

    approved = (
        service()
        .approve(
            awaiting_approval(),
            approver=FundingApprover(identity="staff-1", is_authenticated=True),
            approved_at=NOW,
            balance=balance(),
            ledger=ledger(),
        )
        .proposal
    )
    first = adapter.acknowledge(approved, acknowledged_at=NOW + timedelta(seconds=1))
    repeated = adapter.acknowledge(approved, acknowledged_at=NOW + timedelta(seconds=2))

    assert first.accepted
    assert first.proposal.state is FundingProposalState.ACKNOWLEDGED
    assert repeated.accepted and repeated.duplicate
    assert repeated.proposal == first.proposal
    conflict = adapter.acknowledge(
        approved.model_copy(update={"target_context": "owner:other"}),
        acknowledged_at=NOW + timedelta(seconds=2),
    )
    assert not conflict.accepted
    assert conflict.reason_code == "acknowledgement_conflict"
