"""Durable operator boundary for capital attention, approval, and manual action."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from uuid import UUID

from django.db import DatabaseError, transaction
from pydantic import AwareDatetime, PositiveInt, ValidationError

from qbet.bank.balances import BankBalance
from qbet.bank.funding import (
    BankFundingProposal,
    BankFundingProposalService,
    FundingApprover,
    FundingProposalState,
)
from qbet.bank.movement import (
    CapitalMovementInstruction,
    CapitalMovementRecord,
    CapitalMovementService,
)
from qbet.domain.models import DomainModel, Identifier, PositiveDecimal
from qbet.storage.capital_movement import CapitalMovementRepository
from qbet.storage.ledger import PortfolioLedgerRepository
from qbet.storage.models import (
    CapitalFundingProposalRow,
    NotificationInboxDeliveryRow,
)


class CapitalActionMethod(StrEnum):
    MANUAL = "manual"
    SANDBOX_ADAPTER = "sandbox_adapter"


class CapitalFundingWorkflowRecord(DomainModel):
    """Durable proposal context needed for a later authenticated decision."""

    proposal: BankFundingProposal
    owner_id: Identifier
    balance: BankBalance
    action_method: CapitalActionMethod
    max_amount: PositiveDecimal
    max_balance_age_seconds: PositiveInt


class CapitalFundingWorkflowError(RuntimeError):
    """Raised when durable capital workflow state cannot be used safely."""


class CapitalFundingWorkflowConflict(ValueError):
    """Raised when a replay changes durable proposal identity or state."""


class CapitalFundingWorkflowRepository:
    """Persist attention before approval and keep movement behind a later action."""

    def __init__(
        self,
        *,
        ledger_repository: PortfolioLedgerRepository | None = None,
        movement_repository: CapitalMovementRepository | None = None,
    ) -> None:
        self._ledger_repository = ledger_repository or PortfolioLedgerRepository()
        self._movement_repository = movement_repository or CapitalMovementRepository()

    def publish_attention(
        self,
        record: CapitalFundingWorkflowRecord,
        *,
        requested_at: AwareDatetime,
    ) -> CapitalFundingWorkflowRecord:
        proposal = record.proposal
        if proposal.state is not FundingProposalState.PROPOSED:
            raise CapitalFundingWorkflowConflict("capital_proposal_not_proposed")
        if proposal.attention_created_at is None:
            raise CapitalFundingWorkflowConflict("capital_attention_missing")

        policy = self._policy(record)
        awaiting = policy.request_approval(proposal, requested_at=requested_at)
        if not awaiting.accepted:
            raise CapitalFundingWorkflowConflict(
                awaiting.reason_code or "capital_approval_request_rejected"
            )
        awaiting_record = record.model_copy(update={"proposal": awaiting.proposal})

        try:
            with transaction.atomic():
                row, created = CapitalFundingProposalRow.objects.select_for_update().get_or_create(
                    proposal_id=proposal.id,
                    defaults={
                        "owner_id": record.owner_id,
                        "correlation_id": proposal.correlation_id,
                        "state": proposal.state.value,
                        "action_method": record.action_method.value,
                        "attention_published": False,
                        "payload": record.model_dump(mode="json"),
                    },
                )
                if not created:
                    stored = self._record_from_row(row)
                    if self._canonical(stored) != self._canonical(awaiting_record):
                        raise CapitalFundingWorkflowConflict("capital_proposal_identity_conflict")
                    return stored

                NotificationInboxDeliveryRow.objects.get_or_create(
                    user_id=record.owner_id,
                    task_id=proposal.id,
                    defaults={"category": "funding_attention"},
                )
                row.state = awaiting.proposal.state.value
                row.attention_published = True
                row.payload = awaiting_record.model_dump(mode="json")
                row.save(
                    update_fields=(
                        "state",
                        "attention_published",
                        "payload",
                        "updated_at",
                    )
                )
                return awaiting_record
        except CapitalFundingWorkflowConflict:
            raise
        except (DatabaseError, ValidationError) as error:
            raise CapitalFundingWorkflowError("capital_workflow_unavailable") from error

    def load(self, proposal_id: UUID) -> CapitalFundingWorkflowRecord | None:
        try:
            row = CapitalFundingProposalRow.objects.filter(proposal_id=proposal_id).first()
            return None if row is None else self._record_from_row(row)
        except (DatabaseError, ValidationError) as error:
            raise CapitalFundingWorkflowError("capital_workflow_unavailable") from error

    def list_for(self, owner_id: str) -> tuple[CapitalFundingWorkflowRecord, ...]:
        try:
            rows = CapitalFundingProposalRow.objects.filter(owner_id=owner_id).order_by(
                "-updated_at"
            )
            return tuple(self._record_from_row(row) for row in rows)
        except (DatabaseError, ValidationError) as error:
            raise CapitalFundingWorkflowError("capital_workflow_unavailable") from error

    def decide(
        self,
        proposal_id: UUID,
        *,
        approver: FundingApprover,
        approve: bool,
        decided_at: AwareDatetime,
    ) -> CapitalFundingWorkflowRecord:
        try:
            with transaction.atomic():
                row = CapitalFundingProposalRow.objects.select_for_update().get(
                    proposal_id=proposal_id
                )
                if row.owner_id != approver.identity:
                    raise PermissionError("capital_proposal_owner_mismatch")
                if not row.attention_published:
                    raise CapitalFundingWorkflowConflict("capital_attention_not_published")

                record = self._record_from_row(row)
                proposal = record.proposal

                if proposal.state in {
                    FundingProposalState.APPROVED,
                    FundingProposalState.REJECTED,
                    FundingProposalState.EXPIRED,
                    FundingProposalState.CANCELLED,
                }:
                    return record

                policy = self._policy(record)
                if approve:
                    ledger = self._ledger_repository.load(
                        mode=proposal.target_mode,
                        currency=proposal.currency,
                    )
                    if ledger is None:
                        raise CapitalFundingWorkflowError("capital_ledger_missing")
                    outcome = policy.approve(
                        proposal,
                        approver=approver,
                        approved_at=decided_at,
                        balance=record.balance,
                        ledger=ledger,
                        capital_context=proposal.target_context,
                    )
                else:
                    outcome = policy.reject(proposal, rejected_at=decided_at)

                if not outcome.accepted:
                    raise CapitalFundingWorkflowConflict(
                        outcome.reason_code or "capital_decision_rejected"
                    )

                updated = record.model_copy(update={"proposal": outcome.proposal})
                row.state = outcome.proposal.state.value
                row.payload = updated.model_dump(mode="json")
                row.save(update_fields=("state", "payload", "updated_at"))
                return updated
        except (PermissionError, CapitalFundingWorkflowConflict, CapitalFundingWorkflowError):
            raise
        except CapitalFundingProposalRow.DoesNotExist as error:
            raise KeyError(proposal_id) from error
        except (DatabaseError, ValidationError) as error:
            raise CapitalFundingWorkflowError("capital_workflow_unavailable") from error

    def manual_instruction(
        self,
        proposal_id: UUID,
        *,
        actor: str,
    ) -> CapitalMovementInstruction | None:
        record = self._owned_record(proposal_id, actor)
        if record.action_method is not CapitalActionMethod.MANUAL:
            return None
        return CapitalMovementService().manual_instruction(record.proposal)

    def mark_manual_performed(
        self,
        proposal_id: UUID,
        *,
        actor: str,
        performed_at: AwareDatetime,
    ) -> CapitalMovementRecord:
        record = self._owned_record(proposal_id, actor)
        if record.action_method is not CapitalActionMethod.MANUAL:
            raise CapitalFundingWorkflowConflict("capital_action_not_manual")
        return self._movement_repository.record_manual(
            record.proposal,
            performed_at=performed_at,
        )

    def _owned_record(
        self,
        proposal_id: UUID,
        actor: str,
    ) -> CapitalFundingWorkflowRecord:
        try:
            row = CapitalFundingProposalRow.objects.get(proposal_id=proposal_id)
        except CapitalFundingProposalRow.DoesNotExist as error:
            raise KeyError(proposal_id) from error
        except DatabaseError as error:
            raise CapitalFundingWorkflowError("capital_workflow_unavailable") from error
        if row.owner_id != actor:
            raise PermissionError("capital_proposal_owner_mismatch")
        return self._record_from_row(row)

    @staticmethod
    def _policy(record: CapitalFundingWorkflowRecord) -> BankFundingProposalService:
        return BankFundingProposalService(
            max_amount=record.max_amount,
            max_balance_age=timedelta(seconds=record.max_balance_age_seconds),
        )

    @staticmethod
    def _canonical(record: CapitalFundingWorkflowRecord) -> dict[str, object]:
        values = record.model_dump(mode="python")
        proposal = record.proposal
        if proposal.state is FundingProposalState.AWAITING_APPROVAL:
            proposal = proposal.model_copy(
                update={
                    "state": FundingProposalState.PROPOSED,
                    "lifecycle_at": proposal.created_at,
                }
            )
        values["proposal"] = proposal.model_dump(mode="python")
        return values

    @staticmethod
    def _record_from_row(row: CapitalFundingProposalRow) -> CapitalFundingWorkflowRecord:
        record = CapitalFundingWorkflowRecord.model_validate(row.payload)
        if (
            row.owner_id != record.owner_id
            or row.correlation_id != record.proposal.correlation_id
            or row.state != record.proposal.state.value
            or row.action_method != record.action_method.value
        ):
            raise CapitalFundingWorkflowError("capital_workflow_metadata_mismatch")
        return record
