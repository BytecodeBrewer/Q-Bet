"""Typed, provider-neutral contracts for targeted mode-specific refreshes."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from qbet.domain.models import DomainModel, Identifier


class RequestHandlerMode(StrEnum):
    SIMULATION = "simulation"
    EXECUTION = "execution"


class RevalidationOutcome(StrEnum):
    VALID = "valid"
    CHANGED = "changed"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"


class ResultStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    NOT_YET_AVAILABLE = "not_yet_available"


class ModeRequest(DomainModel):
    """A targeted refresh request, independent from aggregation and capital state."""

    opportunity_id: Identifier
    mode: RequestHandlerMode
    correlation_id: UUID
    lifecycle_id: Identifier


class RevalidationResult(ModeRequest):
    validated_at: AwareDatetime
    outcome: RevalidationOutcome
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_reason_code(self) -> "RevalidationResult":
        if self.outcome is RevalidationOutcome.VALID and self.reason_code is not None:
            raise ValueError("valid revalidation results must not include a reason_code")
        if self.outcome is not RevalidationOutcome.VALID and self.reason_code is None:
            raise ValueError("non-valid revalidation results require a reason_code")
        return self

    @property
    def is_valid(self) -> bool:
        return self.outcome is RevalidationOutcome.VALID


class RequestHandlerResult(ModeRequest):
    observed_at: AwareDatetime
    status: ResultStatus
    reason_code: Identifier | None = None
    result_reference: Identifier | None = None

    @model_validator(mode="after")
    def validates_result_contract(self) -> "RequestHandlerResult":
        if self.status is ResultStatus.SUCCESS:
            if self.reason_code is not None:
                raise ValueError("successful results must not include a reason_code")
            if self.result_reference is None:
                raise ValueError("successful results require a result_reference")
        elif self.reason_code is None:
            raise ValueError("non-successful results require a reason_code")
        return self


class SandboxRevalidationFixture(DomainModel):
    opportunity_id: Identifier
    outcome: RevalidationOutcome
    validated_at: AwareDatetime
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_reason_code(self) -> "SandboxRevalidationFixture":
        RevalidationResult(
            opportunity_id=self.opportunity_id,
            mode=RequestHandlerMode.SIMULATION,
            correlation_id=UUID(int=0),
            lifecycle_id="fixture",
            validated_at=self.validated_at,
            outcome=self.outcome,
            reason_code=self.reason_code,
        )
        return self


class SandboxResultFixture(DomainModel):
    opportunity_id: Identifier
    status: ResultStatus
    observed_at: AwareDatetime
    reason_code: Identifier | None = None
    result_reference: Identifier | None = None

    @model_validator(mode="after")
    def validates_result_contract(self) -> "SandboxResultFixture":
        RequestHandlerResult(
            opportunity_id=self.opportunity_id,
            mode=RequestHandlerMode.SIMULATION,
            correlation_id=UUID(int=0),
            lifecycle_id="fixture",
            observed_at=self.observed_at,
            status=self.status,
            reason_code=self.reason_code,
            result_reference=self.result_reference,
        )
        return self
