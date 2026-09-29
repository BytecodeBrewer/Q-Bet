"""Typed capital requirements and approval-gated movement lifecycle."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, model_validator

from qbet.bank.funding import (
    BankFundingProposal,
    FundingAccountRole,
    FundingDirection,
    FundingProposalState,
)
from qbet.domain.models import Currency, DomainModel, Identifier, PositiveDecimal


class CapitalRequirement(DomainModel):
    """Typed request for capital without granting movement authority."""

    id: UUID
    direction: FundingDirection
    amount: PositiveDecimal
    currency: Currency
    source_role: FundingAccountRole
    destination_role: FundingAccountRole
    source_location: Identifier | None = None
    destination_location: Identifier
    reason: Identifier
    opportunity_id: Identifier | None = None
    correlation_id: UUID
    required_by: AwareDatetime | None = None
    target_mode: Literal["simulation", "execution"]
    target_context: Identifier
    engine: Identifier | None = None
    workflow_reference: Identifier
    already_satisfied: bool = False

    @model_validator(mode="after")
    def validates_roles_and_context(self) -> "CapitalRequirement":
        expected_roles = (
            (FundingAccountRole.BANK_ACCOUNT, FundingAccountRole.PORTFOLIO_LEDGER)
            if self.direction is FundingDirection.FUNDING
            else (FundingAccountRole.PORTFOLIO_LEDGER, FundingAccountRole.BANK_ACCOUNT)
        )
        if (self.source_role, self.destination_role) != expected_roles:
            raise ValueError("capital requirement direction must match source and destination roles")
        if not self.target_context.endswith(f":{self.target_mode}"):
            raise ValueError("capital requirement target_context must match target_mode")
        return self


class CapitalRequirementDecisionKind(StrEnum):
    PROPOSAL = "proposal"
    NO_ACTION = "no_action"
    UNAVAILABLE = "unavailable"


class CapitalAttentionNotice(DomainModel):
    """Notification payload saying capital needs attention, not permission to move it."""

    requirement_id: UUID
    correlation_id: UUID
    direction: FundingDirection
    amount: PositiveDecimal
    currency: Currency
    source_location: Identifier | None = None
    destination_location: Identifier
    reason: Identifier
    requires_approval: bool = True


class CapitalRequirementDecision(DomainModel):
    """Result of translating a capital requirement into a proposal or explicit non-action."""

    requirement: CapitalRequirement
    kind: CapitalRequirementDecisionKind
    proposal: BankFundingProposal | None = None
    attention: CapitalAttentionNotice | None = None
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_decision(self) -> "CapitalRequirementDecision":
        if self.kind is CapitalRequirementDecisionKind.PROPOSAL:
            if self.proposal is None or self.attention is None or self.reason_code is not None:
                raise ValueError("proposal decisions require proposal and attention only")
        elif self.proposal is not None or self.reason_code is None:
            raise ValueError("non-proposal decisions require a reason code")
        if self.kind is CapitalRequirementDecisionKind.NO_ACTION and self.attention is not None:
            raise ValueError("no-action decisions must not request attention")
        return self


class CapitalRequirementService:
    """Create one explicit funding/withdrawal proposal without moving capital."""

    def attention(self, requirement: CapitalRequirement) -> CapitalAttentionNotice:
        return CapitalAttentionNotice(
            requirement_id=requirement.id,
            correlation_id=requirement.correlation_id,
            direction=requirement.direction,
            amount=requirement.amount,
            currency=requirement.currency,
            source_location=requirement.source_location,
            destination_location=requirement.destination_location,
            reason=requirement.reason,
        )

    def propose(
        self,
        requirement: CapitalRequirement,
        *,
        created_at: AwareDatetime,
        expires_at: AwareDatetime,
    ) -> CapitalRequirementDecision:
        if requirement.already_satisfied:
            return CapitalRequirementDecision(
                requirement=requirement,
                kind=CapitalRequirementDecisionKind.NO_ACTION,
                reason_code="already_funded",
            )
        if requirement.required_by is not None and created_at > requirement.required_by:
            return CapitalRequirementDecision(
                requirement=requirement,
                kind=CapitalRequirementDecisionKind.UNAVAILABLE,
                attention=self.attention(requirement),
                reason_code="requirement_deadline_elapsed",
            )
        if requirement.source_location is None:
            return CapitalRequirementDecision(
                requirement=requirement,
                kind=CapitalRequirementDecisionKind.UNAVAILABLE,
                attention=self.attention(requirement),
                reason_code="capital_source_unavailable",
            )

        proposal = BankFundingProposal(
            id=uuid5(NAMESPACE_URL, f"qbet:capital-requirement:{requirement.id}"),
            requirement_id=requirement.id,
            direction=requirement.direction,
            source_role=requirement.source_role,
            destination_role=requirement.destination_role,
            source_location=requirement.source_location,
            destination_location=requirement.destination_location,
            amount=requirement.amount,
            currency=requirement.currency,
            reason=requirement.reason,
            opportunity_id=requirement.opportunity_id,
            target_mode=requirement.target_mode,
            target_context=requirement.target_context,
            correlation_id=requirement.correlation_id,
            required_by=requirement.required_by,
            engine=requirement.engine,
            workflow_reference=requirement.workflow_reference,
            attention_created_at=created_at,
            created_at=created_at,
            expires_at=expires_at,
            lifecycle_at=created_at,
        )
        return CapitalRequirementDecision(
            requirement=requirement,
            kind=CapitalRequirementDecisionKind.PROPOSAL,
            proposal=proposal,
            attention=self.attention(requirement),
        )


class CapitalMovementMethod(StrEnum):
    MANUAL = "manual"
    SANDBOX_ADAPTER = "sandbox_adapter"


class CapitalMovementState(StrEnum):
    PENDING = "pending"
    RECONCILED = "reconciled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    MISMATCH = "mismatch"


class CapitalMovementObservationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    MISMATCH = "mismatch"


class CapitalMovementInstruction(DomainModel):
    """Exact operator instruction available only after explicit approval."""

    proposal_id: UUID
    direction: FundingDirection
    amount: PositiveDecimal
    currency: Currency
    source_location: Identifier
    destination_location: Identifier
    reason: Identifier


class CapitalMovementObservation(DomainModel):
    """Later authoritative/observed result used for reconciliation."""

    status: CapitalMovementObservationStatus
    observed_at: AwareDatetime
    amount: PositiveDecimal | None = None
    currency: Currency | None = None
    source_location: Identifier | None = None
    destination_location: Identifier | None = None
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_confirmed_observation(self) -> "CapitalMovementObservation":
        if self.status is CapitalMovementObservationStatus.CONFIRMED:
            if (
                self.amount is None
                or self.currency is None
                or self.source_location is None
                or self.destination_location is None
            ):
                raise ValueError("confirmed movement observations require exact movement details")
        return self


class CapitalMovementRecord(DomainModel):
    """Durable movement state. Pending never implies authoritative ledger settlement."""

    id: UUID
    proposal: BankFundingProposal
    method: CapitalMovementMethod
    state: CapitalMovementState
    performed_at: AwareDatetime
    provider_reference: Identifier | None = None
    observation: CapitalMovementObservation | None = None
    reason_code: Identifier | None = None
    ledger_applied: bool = False
    duplicate: bool = False

    @model_validator(mode="after")
    def validates_record(self) -> "CapitalMovementRecord":
        if self.proposal.state is not FundingProposalState.APPROVED:
            raise ValueError("capital movement requires an approved proposal")
        approval = self.proposal.approval
        assert approval is not None
        if self.performed_at < approval.approved_at:
            raise ValueError("capital movement cannot precede approval")
        if self.method is CapitalMovementMethod.MANUAL and self.provider_reference is not None:
            raise ValueError("manual capital movement cannot carry a provider reference")
        if self.method is CapitalMovementMethod.SANDBOX_ADAPTER:
            if self.state is CapitalMovementState.PENDING and self.provider_reference is None:
                raise ValueError("adapter acknowledgement requires a provider reference")
        if self.ledger_applied and self.state is not CapitalMovementState.RECONCILED:
            raise ValueError("only reconciled capital movement may be applied to ledger")
        return self


class CapitalMovementService:
    """Pure movement transitions; persistence owns idempotency and ledger mutation."""

    def manual_instruction(
        self, proposal: BankFundingProposal
    ) -> CapitalMovementInstruction | None:
        if proposal.state is not FundingProposalState.APPROVED:
            return None
        if proposal.source_location is None or proposal.destination_location is None:
            return None
        return CapitalMovementInstruction(
            proposal_id=proposal.id,
            direction=proposal.direction,
            amount=proposal.amount,
            currency=proposal.currency,
            source_location=proposal.source_location,
            destination_location=proposal.destination_location,
            reason=proposal.reason,
        )

    def mark_manual_performed(
        self,
        proposal: BankFundingProposal,
        *,
        performed_at: AwareDatetime,
    ) -> CapitalMovementRecord:
        if self.manual_instruction(proposal) is None:
            raise ValueError("capital_movement_instruction_unavailable")
        if performed_at >= proposal.expires_at:
            raise ValueError("capital_movement_proposal_expired")
        return CapitalMovementRecord(
            id=_movement_id(proposal.id),
            proposal=proposal,
            method=CapitalMovementMethod.MANUAL,
            state=CapitalMovementState.PENDING,
            performed_at=performed_at,
        )

    def acknowledge_adapter(
        self,
        proposal: BankFundingProposal,
        *,
        provider_reference: Identifier,
        acknowledged_at: AwareDatetime,
    ) -> CapitalMovementRecord:
        if proposal.state is not FundingProposalState.APPROVED:
            raise ValueError("capital_movement_requires_approval")
        if proposal.source_location is None or proposal.destination_location is None:
            raise ValueError("capital_movement_locations_missing")
        return CapitalMovementRecord(
            id=_movement_id(proposal.id),
            proposal=proposal,
            method=CapitalMovementMethod.SANDBOX_ADAPTER,
            state=CapitalMovementState.PENDING,
            performed_at=acknowledged_at,
            provider_reference=provider_reference,
        )

    def adapter_failed(
        self,
        proposal: BankFundingProposal,
        *,
        reason_code: Identifier,
        failed_at: AwareDatetime,
    ) -> CapitalMovementRecord:
        if proposal.state is not FundingProposalState.APPROVED:
            raise ValueError("capital_movement_requires_approval")
        return CapitalMovementRecord(
            id=_movement_id(proposal.id),
            proposal=proposal,
            method=CapitalMovementMethod.SANDBOX_ADAPTER,
            state=CapitalMovementState.FAILED,
            performed_at=failed_at,
            reason_code=reason_code,
        )

    def reconcile(
        self,
        record: CapitalMovementRecord,
        observation: CapitalMovementObservation,
    ) -> CapitalMovementRecord:
        if observation.observed_at < record.performed_at:
            raise ValueError("capital_movement_observation_before_performed")

        if record.state is not CapitalMovementState.PENDING:
            if record.observation == observation:
                return record.model_copy(update={"duplicate": True})
            raise ValueError("capital_movement_reconciliation_conflict")

        state = {
            CapitalMovementObservationStatus.PENDING: CapitalMovementState.PENDING,
            CapitalMovementObservationStatus.CONFIRMED: CapitalMovementState.RECONCILED,
            CapitalMovementObservationStatus.FAILED: CapitalMovementState.FAILED,
            CapitalMovementObservationStatus.CANCELLED: CapitalMovementState.CANCELLED,
            CapitalMovementObservationStatus.EXPIRED: CapitalMovementState.EXPIRED,
            CapitalMovementObservationStatus.MISMATCH: CapitalMovementState.MISMATCH,
        }[observation.status]
        reason_code = observation.reason_code

        if observation.status is CapitalMovementObservationStatus.CONFIRMED:
            if not _observation_matches(record.proposal, observation):
                state = CapitalMovementState.MISMATCH
                reason_code = "capital_movement_reconciliation_mismatch"

        values = record.model_dump(mode="python")
        values.update(
            {
                "state": state,
                "observation": observation,
                "reason_code": reason_code,
                "duplicate": False,
            }
        )
        return CapitalMovementRecord.model_validate(values)


def _movement_id(proposal_id: UUID) -> UUID:
    return uuid5(NAMESPACE_URL, f"qbet:capital-movement:{proposal_id}")


def _observation_matches(
    proposal: BankFundingProposal,
    observation: CapitalMovementObservation,
) -> bool:
    return (
        observation.amount == proposal.amount
        and observation.currency == proposal.currency
        and observation.source_location == proposal.source_location
        and observation.destination_location == proposal.destination_location
    )
