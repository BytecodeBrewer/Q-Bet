from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from qbet.bank.funding import (
    BankFundingProposal,
    FundingAccountRole,
    FundingApproval,
    FundingApprover,
    FundingDirection,
    FundingProposalState,
)
from qbet.bank.movement import (
    CapitalMovementObservation,
    CapitalMovementObservationStatus,
    CapitalMovementService,
    CapitalMovementState,
    CapitalRequirement,
    CapitalRequirementDecisionKind,
    CapitalRequirementService,
)

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
REQUIREMENT_ID = UUID("11111111-2222-3333-4444-555555555555")
PROPOSAL_ID = UUID("87654321-4321-8765-4321-876543218765")
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def requirement(**changes: object) -> CapitalRequirement:
    values: dict[str, object] = {
        "id": REQUIREMENT_ID,
        "direction": FundingDirection.FUNDING,
        "amount": Decimal("25"),
        "currency": "EUR",
        "source_role": FundingAccountRole.BANK_ACCOUNT,
        "destination_role": FundingAccountRole.PORTFOLIO_LEDGER,
        "source_location": "bunq-***1234",
        "destination_location": "owner:simulation",
        "reason": "capital_shortage",
        "opportunity_id": "opportunity-1",
        "correlation_id": CORRELATION_ID,
        "required_by": NOW + timedelta(minutes=20),
        "target_mode": "simulation",
        "target_context": "owner:simulation",
        "engine": "sports_capital",
        "workflow_reference": "workflow-1",
    }
    values.update(changes)
    return CapitalRequirement.model_validate(values)


def approved_proposal(**changes: object) -> BankFundingProposal:
    values: dict[str, object] = {
        "id": PROPOSAL_ID,
        "requirement_id": REQUIREMENT_ID,
        "direction": FundingDirection.FUNDING,
        "source_role": FundingAccountRole.BANK_ACCOUNT,
        "destination_role": FundingAccountRole.PORTFOLIO_LEDGER,
        "source_location": "bunq-***1234",
        "destination_location": "owner:simulation",
        "amount": Decimal("25"),
        "currency": "EUR",
        "reason": "capital_shortage",
        "opportunity_id": "opportunity-1",
        "target_mode": "simulation",
        "target_context": "owner:simulation",
        "correlation_id": CORRELATION_ID,
        "required_by": NOW + timedelta(minutes=20),
        "engine": "sports_capital",
        "workflow_reference": "workflow-1",
        "created_at": NOW - timedelta(minutes=2),
        "expires_at": NOW + timedelta(minutes=20),
        "state": FundingProposalState.APPROVED,
        "lifecycle_at": NOW - timedelta(minutes=1),
        "approval": FundingApproval(
            approver=FundingApprover(identity="owner", is_authenticated=True),
            approved_at=NOW - timedelta(minutes=1),
        ),
    }
    values.update(changes)
    return BankFundingProposal.model_validate(values)


def confirmed_observation(**changes: object) -> CapitalMovementObservation:
    values: dict[str, object] = {
        "status": CapitalMovementObservationStatus.CONFIRMED,
        "observed_at": NOW + timedelta(minutes=2),
        "source": "bank_transaction_feed",
        "evidence_reference": "transaction-***4321",
        "amount": Decimal("25"),
        "currency": "EUR",
        "source_location": "bunq-***1234",
        "destination_location": "owner:simulation",
    }
    values.update(changes)
    return CapitalMovementObservation.model_validate(values)


def test_requirement_produces_exact_correlated_proposal() -> None:
    decision = CapitalRequirementService().propose(
        requirement(),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )

    assert decision.kind is CapitalRequirementDecisionKind.PROPOSAL
    assert decision.proposal is not None
    proposal = decision.proposal
    assert proposal.requirement_id == REQUIREMENT_ID
    assert proposal.correlation_id == CORRELATION_ID
    assert proposal.opportunity_id == "opportunity-1"
    assert proposal.source_location == "bunq-***1234"
    assert proposal.destination_location == "owner:simulation"
    assert proposal.amount == Decimal("25")
    assert proposal.reason == "capital_shortage"
    assert proposal.engine == "sports_capital"
    assert proposal.workflow_reference == "workflow-1"
    assert proposal.attention_created_at == NOW
    assert decision.attention is not None
    assert decision.attention.requires_approval


def test_requirement_surfaces_no_action_and_missing_source_explicitly() -> None:
    service = CapitalRequirementService()

    satisfied = service.propose(
        requirement(already_satisfied=True),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )
    unavailable = service.propose(
        requirement(source_location=None),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )

    assert satisfied.kind is CapitalRequirementDecisionKind.NO_ACTION
    assert satisfied.reason_code == "already_funded"
    assert satisfied.proposal is None
    assert unavailable.kind is CapitalRequirementDecisionKind.UNAVAILABLE
    assert unavailable.reason_code == "capital_source_unavailable"
    assert unavailable.proposal is None
    assert unavailable.attention is not None


def test_attention_is_not_an_execution_instruction_and_rejected_proposal_has_none() -> None:
    attention = CapitalRequirementService().attention(requirement())
    rejected = approved_proposal(
        state=FundingProposalState.REJECTED,
        lifecycle_at=NOW,
        approval=None,
    )

    instruction = CapitalMovementService().manual_instruction(rejected)

    assert attention.requires_approval
    assert attention.destination_location == "owner:simulation"
    assert instruction is None


def test_manual_performed_becomes_pending_without_claiming_reconciliation() -> None:
    service = CapitalMovementService()
    proposal = approved_proposal()

    instruction = service.manual_instruction(proposal)
    movement = service.mark_manual_performed(
        proposal,
        performed_at=NOW,
    )

    assert instruction is not None
    assert instruction.amount == Decimal("25")
    assert instruction.source_location == "bunq-***1234"
    assert movement.state is CapitalMovementState.PENDING
    assert movement.observation is None
    assert not movement.ledger_applied


def test_adapter_acknowledgement_is_pending_and_requires_approval() -> None:
    service = CapitalMovementService()
    pending = service.acknowledge_adapter(
        approved_proposal(),
        provider_reference="bunq-payment-0123456789ab",
        acknowledged_at=NOW,
    )

    assert pending.state is CapitalMovementState.PENDING
    assert not pending.ledger_applied

    with pytest.raises(ValueError, match="requires_approval"):
        service.acknowledge_adapter(
            approved_proposal(
                state=FundingProposalState.REJECTED,
                lifecycle_at=NOW,
                approval=None,
            ),
            provider_reference="bunq-payment-0123456789ab",
            acknowledged_at=NOW,
        )


def test_reconciliation_confirms_or_surfaces_mismatch_without_fabricating_ledger_state() -> None:
    service = CapitalMovementService()
    pending = service.mark_manual_performed(approved_proposal(), performed_at=NOW)

    reconciled = service.reconcile(pending, confirmed_observation())
    mismatch = service.reconcile(
        pending,
        confirmed_observation(amount=Decimal("24")),
    )

    assert reconciled.state is CapitalMovementState.RECONCILED
    assert not reconciled.ledger_applied
    assert mismatch.state is CapitalMovementState.MISMATCH
    assert mismatch.reason_code == "capital_movement_reconciliation_mismatch"
    assert not mismatch.ledger_applied


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (CapitalMovementObservationStatus.PENDING, CapitalMovementState.PENDING),
        (CapitalMovementObservationStatus.FAILED, CapitalMovementState.FAILED),
        (CapitalMovementObservationStatus.CANCELLED, CapitalMovementState.CANCELLED),
        (CapitalMovementObservationStatus.EXPIRED, CapitalMovementState.EXPIRED),
        (CapitalMovementObservationStatus.MISMATCH, CapitalMovementState.MISMATCH),
    ],
)
def test_reconciliation_maps_non_confirmed_outcomes_without_ledger_authority(
    status: CapitalMovementObservationStatus,
    expected: CapitalMovementState,
) -> None:
    pending = CapitalMovementService().mark_manual_performed(
        approved_proposal(),
        performed_at=NOW,
    )
    observation = CapitalMovementObservation(
        status=status,
        observed_at=NOW + timedelta(minutes=1),
        source="reconciliation_worker",
        reason_code="observed_state",
    )

    result = CapitalMovementService().reconcile(pending, observation)

    assert result.state is expected
    assert not result.ledger_applied
