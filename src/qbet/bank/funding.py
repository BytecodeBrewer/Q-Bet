"""Approval-gated, deterministic bank funding proposals with no money movement."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from qbet.bank.balances import BankBalance
from qbet.data.models import FreshnessStatus
from qbet.domain.models import Currency, DomainModel, Identifier, PositiveDecimal
from qbet.ledger import PortfolioLedger


class FundingDirection(StrEnum):
    FUNDING = "funding"
    WITHDRAWAL = "withdrawal"


class FundingAccountRole(StrEnum):
    BANK_ACCOUNT = "bank_account"
    PORTFOLIO_LEDGER = "portfolio_ledger"


class FundingProposalState(StrEnum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    ACKNOWLEDGED = "acknowledged"


class FundingApprover(DomainModel):
    """Identity presented for an explicit funding approval."""

    identity: Identifier
    is_authenticated: bool


class FundingApproval(DomainModel):
    approver: FundingApprover
    approved_at: AwareDatetime


class BankFundingProposal(DomainModel):
    """A future transfer proposal; it has no authority to mutate capital."""

    id: UUID
    direction: FundingDirection
    source_role: FundingAccountRole
    destination_role: FundingAccountRole
    amount: PositiveDecimal
    currency: Currency
    reason: Identifier
    target_mode: Literal["simulation", "execution"]
    target_context: Identifier
    correlation_id: UUID
    created_at: AwareDatetime
    expires_at: AwareDatetime
    state: FundingProposalState = FundingProposalState.PROPOSED
    lifecycle_at: AwareDatetime
    approval: FundingApproval | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def amount_is_not_binary_float(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("funding amounts must not use binary floating point")
        return value

    @field_validator("amount")
    @classmethod
    def amount_is_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("funding amounts must be finite")
        return value

    @model_validator(mode="after")
    def validates_lifecycle(self) -> "BankFundingProposal":
        if self.source_role is self.destination_role:
            raise ValueError("funding source and destination roles must differ")
        expected_roles = (
            (FundingAccountRole.BANK_ACCOUNT, FundingAccountRole.PORTFOLIO_LEDGER)
            if self.direction is FundingDirection.FUNDING
            else (FundingAccountRole.PORTFOLIO_LEDGER, FundingAccountRole.BANK_ACCOUNT)
        )
        if (self.source_role, self.destination_role) != expected_roles:
            raise ValueError("funding direction must match source and destination roles")
        if not self.target_context.endswith(f":{self.target_mode}"):
            raise ValueError("funding target_context must match target_mode")
        if self.expires_at <= self.created_at:
            raise ValueError("funding proposals must expire after creation")
        if self.lifecycle_at < self.created_at:
            raise ValueError("funding lifecycle time cannot precede creation")
        requires_approval = self.state in {
            FundingProposalState.APPROVED,
            FundingProposalState.ACKNOWLEDGED,
        }
        if requires_approval != (self.approval is not None):
            raise ValueError("approved funding states require an approval only")
        if self.approval is not None:
            if not self.approval.approver.is_authenticated:
                raise ValueError("funding approval requires an authenticated approver")
            if not self.created_at <= self.approval.approved_at < self.expires_at:
                raise ValueError("funding approval must be recorded before proposal expiry")
            if self.lifecycle_at < self.approval.approved_at:
                raise ValueError("funding lifecycle time cannot precede approval")
        return self


class FundingProposalOutcome(DomainModel):
    """Pure transition or validation result; the embedded proposal remains immutable."""

    proposal: BankFundingProposal
    accepted: bool
    reason_code: Identifier | None = None
    duplicate: bool = False

    @model_validator(mode="after")
    def validates_outcome(self) -> "FundingProposalOutcome":
        if self.accepted == (self.reason_code is not None):
            raise ValueError("accepted funding outcomes must have no reason_code")
        if self.duplicate and not self.accepted:
            raise ValueError("only accepted funding outcomes may be duplicates")
        return self


class BankFundingProposalService:
    """Pure funding policy validation against observed balance and ledger context."""

    def __init__(self, *, max_amount: PositiveDecimal, max_balance_age: timedelta) -> None:
        if max_balance_age < timedelta(0):
            raise ValueError("max_balance_age cannot be negative")
        self._max_amount = max_amount
        self._max_balance_age = max_balance_age

    def request_approval(
        self, proposal: BankFundingProposal, *, requested_at: AwareDatetime
    ) -> FundingProposalOutcome:
        if proposal.state is not FundingProposalState.PROPOSED:
            return _rejected(proposal, "proposal_not_proposed")
        if requested_at < proposal.created_at:
            return _rejected(proposal, "transition_before_creation")
        if requested_at < proposal.lifecycle_at:
            return _rejected(proposal, "transition_before_current_lifecycle")
        if requested_at >= proposal.expires_at:
            return _rejected(
                _transition(proposal, FundingProposalState.EXPIRED, requested_at),
                "proposal_expired",
            )
        return _accepted(
            _transition(proposal, FundingProposalState.AWAITING_APPROVAL, requested_at)
        )

    def approve(
        self,
        proposal: BankFundingProposal,
        *,
        approver: FundingApprover,
        approved_at: AwareDatetime,
        balance: BankBalance,
        ledger: PortfolioLedger,
        capital_context: Identifier,
    ) -> FundingProposalOutcome:
        approval = FundingApproval(approver=approver, approved_at=approved_at)
        if proposal.state is FundingProposalState.APPROVED:
            if proposal.approval == approval:
                return _accepted(proposal, duplicate=True)
            return _rejected(proposal, "approval_conflict")
        if proposal.state is not FundingProposalState.AWAITING_APPROVAL:
            return _rejected(proposal, "proposal_not_awaiting_approval")
        reason = self._validation_reason(
            proposal,
            approver,
            approved_at,
            balance,
            ledger,
            capital_context,
        )
        if reason is not None:
            expired = reason == "proposal_expired"
            current = (
                _transition(proposal, FundingProposalState.EXPIRED, approved_at)
                if expired
                else proposal
            )
            return _rejected(current, reason)
        return _accepted(
            _transition(
                proposal,
                FundingProposalState.APPROVED,
                approved_at,
                approval=approval,
            )
        )

    def reject(
        self, proposal: BankFundingProposal, *, rejected_at: AwareDatetime
    ) -> FundingProposalOutcome:
        if proposal.state is not FundingProposalState.AWAITING_APPROVAL:
            return _rejected(proposal, "proposal_not_awaiting_approval")
        if rejected_at < proposal.created_at:
            return _rejected(proposal, "transition_before_creation")
        if rejected_at < proposal.lifecycle_at:
            return _rejected(proposal, "transition_before_current_lifecycle")
        if rejected_at >= proposal.expires_at:
            return _rejected(
                _transition(proposal, FundingProposalState.EXPIRED, rejected_at),
                "proposal_expired",
            )
        return _accepted(_transition(proposal, FundingProposalState.REJECTED, rejected_at))

    def cancel(
        self, proposal: BankFundingProposal, *, cancelled_at: AwareDatetime
    ) -> FundingProposalOutcome:
        if proposal.state not in {
            FundingProposalState.PROPOSED,
            FundingProposalState.AWAITING_APPROVAL,
        }:
            return _rejected(proposal, "proposal_not_cancellable")
        if cancelled_at < proposal.created_at:
            return _rejected(proposal, "transition_before_creation")
        if cancelled_at < proposal.lifecycle_at:
            return _rejected(proposal, "transition_before_current_lifecycle")
        if cancelled_at >= proposal.expires_at:
            return _rejected(
                _transition(proposal, FundingProposalState.EXPIRED, cancelled_at),
                "proposal_expired",
            )
        return _accepted(_transition(proposal, FundingProposalState.CANCELLED, cancelled_at))

    def expire(
        self, proposal: BankFundingProposal, *, observed_at: AwareDatetime
    ) -> FundingProposalOutcome:
        if proposal.state not in {
            FundingProposalState.PROPOSED,
            FundingProposalState.AWAITING_APPROVAL,
        }:
            return _rejected(proposal, "proposal_not_expirable")
        if observed_at < proposal.created_at:
            return _rejected(proposal, "transition_before_creation")
        if observed_at < proposal.lifecycle_at:
            return _rejected(proposal, "transition_before_current_lifecycle")
        if observed_at < proposal.expires_at:
            return _rejected(proposal, "proposal_not_expired")
        return _accepted(_transition(proposal, FundingProposalState.EXPIRED, observed_at))

    def _validation_reason(
        self,
        proposal: BankFundingProposal,
        approver: FundingApprover,
        approved_at: AwareDatetime,
        balance: BankBalance,
        ledger: PortfolioLedger,
        capital_context: Identifier,
    ) -> Identifier | None:
        if not approver.is_authenticated:
            return "approver_not_authenticated"
        if approved_at < proposal.created_at:
            return "approval_before_creation"
        if approved_at < proposal.lifecycle_at:
            return "transition_before_current_lifecycle"
        if approved_at >= proposal.expires_at:
            return "proposal_expired"
        if proposal.amount > self._max_amount:
            return "amount_exceeds_limit"
        if balance.currency != proposal.currency or ledger.balance.currency != proposal.currency:
            return "currency_mismatch"
        if balance.correlation_id != proposal.correlation_id:
            return "balance_correlation_mismatch"
        if capital_context != proposal.target_context:
            return "capital_context_mismatch"
        if ledger.balance.mode != proposal.target_mode:
            return "capital_context_mismatch"
        if (
            balance.freshness is not FreshnessStatus.FRESH
            or balance.observed_at > approved_at
            or approved_at - balance.observed_at > self._max_balance_age
        ):
            return "balance_stale"
        available = (
            balance.available_balance
            if proposal.direction is FundingDirection.FUNDING
            else ledger.balance.available
        )
        if proposal.amount > available:
            return "insufficient_available_capital"
        return None


class DeterministicFundingSandboxAdapter:
    """Local acknowledgement recorder; it cannot contact a bank or move funds."""

    def __init__(self) -> None:
        self._acknowledgements: dict[UUID, tuple[BankFundingProposal, FundingProposalOutcome]] = {}

    def acknowledge(
        self, proposal: BankFundingProposal, *, acknowledged_at: AwareDatetime
    ) -> FundingProposalOutcome:
        previous = self._acknowledgements.get(proposal.id)
        if previous is not None:
            acknowledged_proposal, outcome = previous
            if proposal != acknowledged_proposal:
                return _rejected(proposal, "acknowledgement_conflict")
            return outcome.model_copy(update={"duplicate": True})
        if proposal.state is not FundingProposalState.APPROVED:
            return _rejected(proposal, "proposal_not_approved")
        approval = proposal.approval
        assert approval is not None
        if acknowledged_at < proposal.created_at:
            return _rejected(proposal, "transition_before_creation")
        if acknowledged_at < approval.approved_at:
            return _rejected(proposal, "acknowledgement_before_approval")
        if acknowledged_at < proposal.lifecycle_at:
            return _rejected(proposal, "transition_before_current_lifecycle")
        if acknowledged_at >= proposal.expires_at:
            return _rejected(proposal, "proposal_expired")
        outcome = _accepted(
            _transition(
                proposal,
                FundingProposalState.ACKNOWLEDGED,
                acknowledged_at,
                approval=approval,
            )
        )
        self._acknowledgements[proposal.id] = (proposal, outcome)
        return outcome


def _transition(
    proposal: BankFundingProposal,
    state: FundingProposalState,
    lifecycle_at: AwareDatetime,
    *,
    approval: FundingApproval | None = None,
) -> BankFundingProposal:
    values = proposal.model_dump(mode="python")
    values.update({"state": state, "lifecycle_at": lifecycle_at, "approval": approval})
    return BankFundingProposal.model_validate(values)


def _accepted(proposal: BankFundingProposal, *, duplicate: bool = False) -> FundingProposalOutcome:
    return FundingProposalOutcome(proposal=proposal, accepted=True, duplicate=duplicate)


def _rejected(proposal: BankFundingProposal, reason_code: Identifier) -> FundingProposalOutcome:
    return FundingProposalOutcome(proposal=proposal, accepted=False, reason_code=reason_code)
