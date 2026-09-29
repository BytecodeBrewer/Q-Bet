"""Durable bunq sandbox provider feedback staged for later capital reconciliation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import re
from uuid import UUID

from django.db import DatabaseError, transaction
from pydantic import AwareDatetime, ValidationError, model_validator

from qbet.bank.bunq import (
    BunqSandboxFundingAdapter,
    BunqSandboxPaymentResult,
    BunqTransport,
)
from qbet.bank.funding import BankFundingProposal, FundingDirection, FundingProposalState
from qbet.domain.models import DomainModel
from qbet.storage.capital_movement import CapitalMovementRepository
from qbet.storage.models import SandboxFundingOutcomeRow


_REDACTED_BUNQ_REFERENCE = re.compile(r"^bunq-payment-[0-9a-f]{12}$")
_PROVIDER_CLAIM_REASON = "sandbox_funding_provider_claimed"


class SandboxFundingFeedbackPersistenceError(RuntimeError):
    """Raised when durable sandbox funding feedback cannot be restored or applied safely."""


class SandboxFundingFeedbackConflict(ValueError):
    """Raised when one proposal identity is replayed with conflicting provider data."""


class SandboxFundingFeedbackRecord(DomainModel):
    """Canonical provider outcome; provider acknowledgement is not ledger settlement."""

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
    """Execute one fake-money provider write and persist a pending movement for reconciliation."""

    def __init__(
        self,
        *,
        transport: BunqTransport,
        recipient_email: str | None,
        feedback_repository: SandboxFundingFeedbackRepository | None = None,
        movement_repository: CapitalMovementRepository | None = None,
        clock: Callable[[], AwareDatetime] | None = None,
    ) -> None:
        self._adapter = BunqSandboxFundingAdapter(
            transport=transport,
            recipient_email=recipient_email,
            target_mode="simulation",
        )
        self._feedback_repository = feedback_repository or SandboxFundingFeedbackRepository()
        self._movement_repository = movement_repository or CapitalMovementRepository()
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, proposal: BankFundingProposal) -> SandboxFundingFeedbackRecord:
        """Execute at most one provider write; acknowledgement remains pending."""

        claim = self._feedback_repository.claim(proposal)
        if not claim.acquired:
            if _is_provider_claim(claim.record):
                raise SandboxFundingFeedbackPersistenceError(
                    "sandbox_funding_provider_outcome_unknown"
                )
            # Historical feedback written before #212 may already have credited the
            # ledger. Never create a new pending movement that could credit it twice.
            if not claim.record.ledger_applied:
                self._persist_movement(claim.record, recorded_at=self._clock())
            return claim.record.model_copy(update={"duplicate": True})

        provider_result = self._adapter.execute(proposal)
        feedback = self._feedback_repository.apply(proposal, provider_result)
        self._persist_movement(feedback, recorded_at=self._clock())
        return feedback

    def _persist_movement(
        self,
        feedback: SandboxFundingFeedbackRecord,
        *,
        recorded_at: AwareDatetime,
    ) -> None:
        result = feedback.provider_result
        if result.sent:
            provider_reference = result.provider_reference
            assert provider_reference is not None
            self._movement_repository.record_adapter_acknowledgement(
                feedback.proposal,
                provider_reference=provider_reference,
                acknowledged_at=recorded_at,
            )
            return

        self._movement_repository.record_adapter_failure(
            feedback.proposal,
            reason_code=result.reason_code or "sandbox_funding_failed",
            failed_at=recorded_at,
        )


class SandboxFundingFeedbackRepository:
    """Persist one bunq sandbox result without fabricating ledger settlement."""

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
                    return incoming

                stored = self._record_from_row(row)
                if stored.proposal != proposal:
                    raise SandboxFundingFeedbackConflict(
                        "sandbox_funding_feedback_conflict"
                    )
                if _is_provider_claim(stored):
                    row.sent = canonical_result.sent
                    row.provider_reference = canonical_result.provider_reference
                    row.reason_code = canonical_result.reason_code
                    row.ledger_applied = False
                    row.payload = incoming.model_dump(mode="json")
                    row.save(
                        update_fields=(
                            "sent",
                            "provider_reference",
                            "reason_code",
                            "ledger_applied",
                            "payload",
                            "updated_at",
                        )
                    )
                    return incoming

                if stored.provider_result != canonical_result:
                    raise SandboxFundingFeedbackConflict(
                        "sandbox_funding_feedback_conflict"
                    )
                return stored.model_copy(update={"duplicate": True})
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
