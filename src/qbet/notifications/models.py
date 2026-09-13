"""Typed customer-safe notification contracts for approved Execution work."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from qbet.domain.models import Currency, DomainModel, Identifier, PositiveDecimal


class NotificationStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
    ACKNOWLEDGED = "acknowledged"
    EXPIRED = "expired"


class NotificationRecipient(DomainModel):
    user_id: Identifier
    email: str = ""
    display_name: Identifier


class ExecutionNotificationInstruction(DomainModel):
    provider: Identifier
    offer_id: Identifier
    amount: PositiveDecimal
    currency: Currency


class ExecutionNotificationTask(DomainModel):
    """One immutable notification task; provider payloads and credentials are excluded."""

    id: UUID
    execution_id: UUID
    correlation_id: UUID
    recipient: NotificationRecipient
    opportunity_id: Identifier
    engine: Identifier
    strategy: Identifier
    instructions: tuple[ExecutionNotificationInstruction, ...] = Field(min_length=1)
    action_starts_at: AwareDatetime
    action_deadline: AwareDatetime
    created_at: AwareDatetime
    lifecycle_at: AwareDatetime
    status: NotificationStatus = NotificationStatus.PENDING
    failure_reason: Identifier | None = None
    sent_at: AwareDatetime | None = None
    acknowledged_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validates_lifecycle(self) -> "ExecutionNotificationTask":
        if self.action_starts_at >= self.action_deadline:
            raise ValueError("notification action window must have a positive duration")
        if self.created_at >= self.action_deadline:
            raise ValueError("notification must be created before the action deadline")
        if self.lifecycle_at < self.created_at:
            raise ValueError("notification lifecycle cannot precede creation")
        if (self.status is NotificationStatus.FAILED) != (self.failure_reason is not None):
            raise ValueError("only failed notifications carry a failure reason")
        if self.status in {NotificationStatus.SENT, NotificationStatus.ACKNOWLEDGED}:
            if self.sent_at is None:
                raise ValueError("sent notification states require sent_at")
        elif self.sent_at is not None:
            raise ValueError("unsent notification states cannot carry sent_at")
        if self.status is NotificationStatus.ACKNOWLEDGED:
            if self.acknowledged_at is None:
                raise ValueError("acknowledged notifications require acknowledged_at")
            if self.sent_at is not None and self.acknowledged_at < self.sent_at:
                raise ValueError("notification acknowledgement cannot precede sending")
        elif self.acknowledged_at is not None:
            raise ValueError("only acknowledged notifications carry acknowledged_at")
        return self


class NotificationOutcome(DomainModel):
    task: ExecutionNotificationTask | None = None
    accepted: bool
    reason_code: Identifier | None = None
    duplicate: bool = False

    @model_validator(mode="after")
    def validates_outcome(self) -> "NotificationOutcome":
        if self.accepted == (self.reason_code is not None):
            raise ValueError("accepted notification outcomes must not carry a reason")
        if self.duplicate and self.task is None:
            raise ValueError("duplicate notification outcomes require a task")
        return self
