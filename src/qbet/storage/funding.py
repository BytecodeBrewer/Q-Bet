"""Durable bunq sandbox funding feedback into the Simulation ledger."""

from __future__ import annotations

from dataclasses import dataclass
import re
from uuid import UUID

from django.db import DatabaseError, transaction
from pydantic import ValidationError, model_validator

from qbet.bank.bunq import (
    BunqSandboxFundingAdapter,
    BunqSandboxPaymentResult,
    BunqTransport,
)
from qbet.bank.funding import BankFundingProposal, FundingDirection, FundingProposalState
from qbet.domain.models import DomainModel
from qbet.ledger import PortfolioLedger
from qbet.storage.models import PortfolioLedgerRow, SandboxFundingOutcomeRow


_REDACTED_BUNQ_REFERENCE = re.compile(r"^bunq-payment-[0-9a-f]{12}$")
_PROVIDER_CLAIM_REASON = "sandbox_funding_provider_claimed"


class SandboxFundingFeedbackPersistenceError(RuntimeError):
    """Raised when durable sandbox funding feedback cannot be restored or applied safely."""


class SandboxFundingFeedbackConflict(ValueError):
    """Raised when one proposal identity is replayed with conflicting provider data."""


class SandboxFundingFeedbackRecord(DomainModel):
    """Canonical provider outcome plus whether its capital feedback reached the ledger."""

    proposal: BankFundingProposal
    provider_result: BunqSandboxPaymentResult
    ledger_applied: bool = False
    duplicate: bool = False

    @model_validator(mode="after")
    def validates_feedback(self) -> "SandboxFundingFeedbackRecord":
        result = self.provider_result
        if self.proposal.id != result.proposal_id:
            raise ValueError("sandbox funding proposal identity mismatch")
        if self.proposal.correlation_id != result.correlation_id:
            raise ValueError("sandbox funding correlation mismatch")
        if self.proposal.state is not FundingProposalState.APPROVED:
            raise ValueError("sandbox funding feedback requires an approved proposal")
        if self.proposal.target_mode != "simulation":
            raise ValueError("sandbox funding feedback requires Simulation context")
        if self.proposal.direction is not FundingDirection.FUNDING:
            raise ValueError("sandbox funding feedback supports funding only")
        if result.test_reference != f"qbet-sandbox-{self.proposal.id}":
            raise ValueError("sandbox funding test reference mismatch")
        if (
            result.provider_reference is not None
            and not _REDACTED_BUNQ_REFERENCE.fullmatch(result.provider_reference)
        ):
            raise ValueError("sandbox funding provider reference must be redacted")
        if self.ledger_applied and not result.sent:
            raise ValueError("failed sandbox funding cannot be applied to the ledger")
        return self


@dataclass(frozen=True)
class _SandboxFundingProviderClaim:
    record: SandboxFundingFeedbackRecord
    acquired: bool


class BunqSandboxSimulationFundingService:
    """Production boundary for sandbox execution plus durable Simulation feedback."""

    def __init__(
        self,
        *,
        transport: BunqTransport,
        recipient_email: str | None,
        feedback_repository: SandboxFundingFeedbackRepository | None = None,
    ) -> None:
        self._adapter = BunqSandboxFundingAdapter(
            transport=transport,
            recipient_email=recipient_email,
            target_mode="simulation",
        )
        self._feedback_repository = feedback_repository or SandboxFundingFeedbackRepository()

    def execute(self, proposal: BankFundingProposal) -> SandboxFundingFeedbackRecord:
        """Execute at most one provider write for a durable funding proposal."""

        claim = self._feedback_repository.claim(proposal)
        if not claim.acquired:
            if _is_provider_claim(claim.record):
                raise SandboxFundingFeedbackPersistenceError(
                    "sandbox_funding_provider_outcome_unknown"
                )
            return claim.record.model_copy(update={"duplicate": True})

        provider_result = self._adapter.execute(proposal)
        return self._feedback_repository.apply(proposal, provider_result)


class SandboxFundingFeedbackRepository:
    """Atomically persist one bunq sandbox result and its Simulation ledger credit."""

    _provider_id = "bunq"

    def claim(self, proposal: BankFundingProposal) -> _SandboxFundingProviderClaim:
        """Durably reserve one proposal before the provider side effect."""

        claim_result = BunqSandboxPaymentResult(
            proposal_id=proposal.id,
            correlation_id=proposal.correlation_id,
            test_reference=f"qbet-sandbox-{proposal.id}",
            sent=False,
            reason_code=_PROVIDER_CLAIM_REASON,
        )
        try:
            incoming = SandboxFundingFeedbackRecord(
                proposal=proposal,
                provider_result=claim_result,
            )
        except ValidationError as error:
            raise SandboxFundingFeedbackConflict(
                "sandbox_funding_feedback_invalid"
            ) from error

        try:
            with transaction.atomic():
                row, created = SandboxFundingOutcomeRow.objects.select_for_update().get_or_create(
                    proposal_id=proposal.id,
                    defaults={
                        "correlation_id": proposal.correlation_id,
                        "provider_id": self._provider_id,
                        "sent": False,
                        "provider_reference": None,
                        "reason_code": _PROVIDER_CLAIM_REASON,
                        "ledger_applied": False,
                        "payload": incoming.model_dump(mode="json"),
                    },
                )
                if created:
                    return _SandboxFundingProviderClaim(
                        record=incoming,
                        acquired=True,
                    )

                stored = self._record_from_row(row)
                if stored.proposal != proposal:
                    raise SandboxFundingFeedbackConflict(
                        "sandbox_funding_feedback_conflict"
                    )
                return _SandboxFundingProviderClaim(
                    record=stored,
                    acquired=False,
                )
        except (DatabaseError, ValidationError) as error:
            raise SandboxFundingFeedbackPersistenceError(
                "sandbox_funding_feedback_unavailable"
            ) from error

    def apply(
        self,
        proposal: BankFundingProposal,
        result: BunqSandboxPaymentResult,
    ) -> SandboxFundingFeedbackRecord:
        canonical_result = result.model_copy(update={"duplicate": False})
        try:
            incoming = SandboxFundingFeedbackRecord(
                proposal=proposal,
                provider_result=canonical_result,
            )
        except ValidationError as error:
            raise SandboxFundingFeedbackConflict("sandbox_funding_feedback_invalid") from error

        try:
            with transaction.atomic():
                row, created = SandboxFundingOutcomeRow.objects.select_for_update().get_or_create(
                    proposal_id=proposal.id,
                    defaults={
                        "correlation_id": proposal.correlation_id,
                        "provider_id": self._provider_id,
                        "sent": canonical_result.sent,
                        "provider_reference": canonical_result.provider_reference,
                        "reason_code": canonical_result.reason_code,
                        "ledger_applied": False,
                        "payload": incoming.model_dump(mode="json"),
                    },
                )
                if created:
                    stored = incoming
                else:
                    stored = self._record_from_row(row)
                    if stored.proposal != proposal:
                        raise SandboxFundingFeedbackConflict(
                            "sandbox_funding_feedback_conflict"
                        )
                    if _is_provider_claim(stored):
                        row.sent = canonical_result.sent
                        row.provider_reference = canonical_result.provider_reference
                        row.reason_code = canonical_result.reason_code
                        row.payload = incoming.model_dump(mode="json")
                        row.save(
                            update_fields=(
                                "sent",
                                "provider_reference",
                                "reason_code",
                                "payload",
                                "updated_at",
                            )
                        )
                        stored = incoming
                    else:
                        if stored.provider_result != canonical_result:
                            raise SandboxFundingFeedbackConflict(
                                "sandbox_funding_feedback_conflict"
                            )
                        if stored.ledger_applied or not canonical_result.sent:
                            return stored.model_copy(update={"duplicate": True})

                if not canonical_result.sent:
                    return stored

                ledger_row = (
                    PortfolioLedgerRow.objects.select_for_update()
                    .filter(mode="simulation", currency=proposal.currency)
                    .first()
                )
                if ledger_row is None:
                    raise SandboxFundingFeedbackPersistenceError(
                        "simulation_ledger_missing"
                    )
                ledger = PortfolioLedger.model_validate(ledger_row.payload)
                updated, decision = ledger.fund_external(
                    command_id=_ledger_command_id(proposal.id),
                    dispatch_id=_ledger_dispatch_id(proposal.id),
                    correlation_id=str(proposal.correlation_id),
                    amount=proposal.amount,
                )
                if not decision.accepted:
                    if decision.reason == "idempotency_conflict":
                        raise SandboxFundingFeedbackConflict(
                            "sandbox_funding_ledger_conflict"
                        )
                    raise SandboxFundingFeedbackPersistenceError(
                        "sandbox_funding_ledger_rejected"
                    )

                ledger_row.payload = updated.model_dump(mode="json")
                ledger_row.save(update_fields=("payload", "updated_at"))

                applied = stored.model_copy(update={"ledger_applied": True})
                row.ledger_applied = True
                row.payload = applied.model_dump(mode="json")
                row.save(update_fields=("ledger_applied", "payload", "updated_at"))
                return applied
        except (DatabaseError, ValidationError) as error:
            raise SandboxFundingFeedbackPersistenceError(
                "sandbox_funding_feedback_unavailable"
            ) from error

    def load(self, proposal_id: UUID) -> SandboxFundingFeedbackRecord | None:
        try:
            row = SandboxFundingOutcomeRow.objects.filter(proposal_id=proposal_id).first()
            return None if row is None else self._record_from_row(row)
        except (DatabaseError, ValidationError) as error:
            raise SandboxFundingFeedbackPersistenceError(
                "sandbox_funding_feedback_unavailable"
            ) from error

    def _record_from_row(self, row: SandboxFundingOutcomeRow) -> SandboxFundingFeedbackRecord:
        record = SandboxFundingFeedbackRecord.model_validate(row.payload)
        result = record.provider_result
        if (
            row.correlation_id != record.proposal.correlation_id
            or row.provider_id != self._provider_id
            or row.sent != result.sent
            or row.provider_reference != result.provider_reference
            or row.reason_code != result.reason_code
            or row.ledger_applied != record.ledger_applied
        ):
            raise SandboxFundingFeedbackPersistenceError(
                "sandbox_funding_feedback_metadata_mismatch"
            )
        return record


def _is_provider_claim(record: SandboxFundingFeedbackRecord) -> bool:
    result = record.provider_result
    return (
        not result.sent
        and result.reason_code == _PROVIDER_CLAIM_REASON
        and not record.ledger_applied
    )


def _ledger_command_id(proposal_id: UUID) -> str:
    return f"funding:{proposal_id}:credit"


def _ledger_dispatch_id(proposal_id: UUID) -> str:
    return f"funding:{proposal_id}"
