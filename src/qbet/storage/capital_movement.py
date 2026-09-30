"""Durable approval-gated capital movement lifecycle and reconciliation."""

from __future__ import annotations

from uuid import UUID

from django.db import DatabaseError, transaction
from pydantic import AwareDatetime, ValidationError

from qbet.bank.funding import BankFundingProposal, FundingDirection
from qbet.bank.movement import (
    CapitalMovementObservation,
    CapitalMovementRecord,
    CapitalMovementService,
    CapitalMovementState,
)
from qbet.ledger import PortfolioLedger
from qbet.storage.models import CapitalMovementRow, PortfolioLedgerRow


class CapitalMovementPersistenceError(RuntimeError):
    """Raised when authoritative capital movement state cannot be persisted safely."""


class CapitalMovementConflict(ValueError):
    """Raised when replayed movement identity or reconciliation data conflict."""


class CapitalMovementRepository:
    """Persist one movement per approved proposal and reconcile it atomically with the ledger."""

    def __init__(self, service: CapitalMovementService | None = None) -> None:
        self._service = service or CapitalMovementService()

    def record_manual(
        self,
        proposal: BankFundingProposal,
        *,
        performed_at: AwareDatetime,
    ) -> CapitalMovementRecord:
        return self._save_pending(
            self._service.mark_manual_performed(proposal, performed_at=performed_at)
        )

    def record_adapter_acknowledgement(
        self,
        proposal: BankFundingProposal,
        *,
        provider_reference: str,
        acknowledged_at: AwareDatetime,
    ) -> CapitalMovementRecord:
        return self._save_pending(
            self._service.acknowledge_adapter(
                proposal,
                provider_reference=provider_reference,
                acknowledged_at=acknowledged_at,
            )
        )

    def record_adapter_failure(
        self,
        proposal: BankFundingProposal,
        *,
        reason_code: str,
        failed_at: AwareDatetime,
    ) -> CapitalMovementRecord:
        return self._save_pending(
            self._service.adapter_failed(
                proposal,
                reason_code=reason_code,
                failed_at=failed_at,
            )
        )

    def load(self, movement_id: UUID) -> CapitalMovementRecord | None:
        try:
            row = CapitalMovementRow.objects.filter(movement_id=movement_id).first()
            return None if row is None else self._record_from_row(row)
        except (DatabaseError, ValidationError) as error:
            raise CapitalMovementPersistenceError("capital_movement_state_unavailable") from error

    def load_by_proposal(self, proposal_id: UUID) -> CapitalMovementRecord | None:
        try:
            row = CapitalMovementRow.objects.filter(proposal_id=proposal_id).first()
            return None if row is None else self._record_from_row(row)
        except (DatabaseError, ValidationError) as error:
            raise CapitalMovementPersistenceError("capital_movement_state_unavailable") from error

    def reconcile(
        self,
        movement_id: UUID,
        observation: CapitalMovementObservation,
    ) -> CapitalMovementRecord:
        try:
            with transaction.atomic():
                row = CapitalMovementRow.objects.select_for_update().get(
                    movement_id=movement_id
                )
                stored = self._record_from_row(row)
                try:
                    transitioned = self._service.reconcile(stored, observation)
                except ValueError as error:
                    raise CapitalMovementConflict(str(error)) from error

                if transitioned.duplicate:
                    return transitioned

                if transitioned.state is CapitalMovementState.RECONCILED:
                    transitioned = self._apply_reconciled_ledger(transitioned)

                row.state = transitioned.state.value
                row.ledger_applied = transitioned.ledger_applied
                row.payload = transitioned.model_dump(mode="json")
                row.save(update_fields=("state", "ledger_applied", "payload", "updated_at"))
                return transitioned
        except CapitalMovementConflict:
            raise
        except CapitalMovementRow.DoesNotExist as error:
            raise CapitalMovementPersistenceError("capital_movement_not_found") from error
        except (DatabaseError, ValidationError) as error:
            raise CapitalMovementPersistenceError("capital_movement_state_unavailable") from error

    def _save_pending(self, incoming: CapitalMovementRecord) -> CapitalMovementRecord:
        try:
            with transaction.atomic():
                row, created = CapitalMovementRow.objects.select_for_update().get_or_create(
                    movement_id=incoming.id,
                    defaults={
                        "proposal_id": incoming.proposal.id,
                        "correlation_id": incoming.proposal.correlation_id,
                        "state": incoming.state.value,
                        "ledger_applied": incoming.ledger_applied,
                        "payload": incoming.model_dump(mode="json"),
                    },
                )
                if created:
                    return incoming

                stored = self._record_from_row(row)
                canonical_stored = stored.model_copy(update={"duplicate": False})
                canonical_incoming = incoming.model_copy(
                    update={
                        "performed_at": stored.performed_at,
                        "duplicate": False,
                    }
                )
                if canonical_stored != canonical_incoming:
                    raise CapitalMovementConflict("capital_movement_identity_conflict")
                return stored.model_copy(update={"duplicate": True})
        except CapitalMovementConflict:
            raise
        except (DatabaseError, ValidationError) as error:
            raise CapitalMovementPersistenceError("capital_movement_state_unavailable") from error

    def _apply_reconciled_ledger(
        self, record: CapitalMovementRecord
    ) -> CapitalMovementRecord:
        proposal = record.proposal
        ledger_row = (
            PortfolioLedgerRow.objects.select_for_update()
            .filter(mode=proposal.target_mode, currency=proposal.currency)
            .first()
        )
        if ledger_row is None:
            raise CapitalMovementPersistenceError("capital_movement_ledger_missing")

        ledger = PortfolioLedger.model_validate(ledger_row.payload)
        kwargs = {
            "command_id": f"capital-movement:{record.id}:reconcile",
            "dispatch_id": f"capital-movement:{record.id}",
            "correlation_id": str(proposal.correlation_id),
            "amount": proposal.amount,
        }
        if proposal.direction is FundingDirection.FUNDING:
            updated, decision = ledger.fund_external(**kwargs)
        else:
            updated, decision = ledger.withdraw_external(**kwargs)

        if not decision.accepted:
            if decision.reason == "idempotency_conflict":
                raise CapitalMovementConflict("capital_movement_ledger_conflict")
            raise CapitalMovementPersistenceError(
                decision.reason or "capital_movement_ledger_rejected"
            )

        ledger_row.payload = updated.model_dump(mode="json")
        ledger_row.save(update_fields=("payload", "updated_at"))
        values = record.model_dump(mode="python")
        values["ledger_applied"] = True
        return CapitalMovementRecord.model_validate(values)

    @staticmethod
    def _record_from_row(row: CapitalMovementRow) -> CapitalMovementRecord:
        record = CapitalMovementRecord.model_validate(row.payload)
        if (
            row.proposal_id != record.proposal.id
            or row.correlation_id != record.proposal.correlation_id
            or row.state != record.state.value
            or row.ledger_applied != record.ledger_applied
        ):
            raise CapitalMovementPersistenceError("capital_movement_metadata_mismatch")
        return record
