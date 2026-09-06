"""Durable, mode-isolated work queues for controlled workflow dispatch."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from qbet.domain.models import DomainModel, Identifier
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.workflow.routing import RoutedWorkItem


class WorkState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RECHECK = "recheck"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class WorkHistoryEvent(DomainModel):
    state: WorkState
    recorded_at: AwareDatetime
    reason: Identifier | None = None


class QueuedWorkItem(DomainModel):
    """One durable, idempotently-addressable dispatch item for exactly one mode."""

    work: RoutedWorkItem
    request: BonusEngineRequest | SportsCapitalEngineRequest
    scheduled_for: AwareDatetime
    expires_at: AwareDatetime
    state: WorkState = WorkState.PENDING
    history: tuple[WorkHistoryEvent, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validates_identity_and_timing(self) -> "QueuedWorkItem":
        if self.work.engine == "bonus" and not isinstance(self.request, BonusEngineRequest):
            raise ValueError("bonus work requires a BonusEngineRequest")
        if self.work.engine == "sports_capital" and not isinstance(
            self.request, SportsCapitalEngineRequest
        ):
            raise ValueError("sports capital work requires a SportsCapitalEngineRequest")
        if self.work.opportunity_id != self.request.opportunity_id:
            raise ValueError("work and request opportunity_id must match")
        if self.scheduled_for >= self.expires_at:
            raise ValueError("scheduled_for must be before expires_at")
        if self.history[-1].state is not self.state:
            raise ValueError("work state must match the latest history event")
        return self

    @classmethod
    def pending(
        cls,
        work: RoutedWorkItem,
        request: BonusEngineRequest | SportsCapitalEngineRequest,
        *,
        scheduled_for: datetime,
        expires_at: datetime,
    ) -> "QueuedWorkItem":
        return cls(
            work=work,
            request=request,
            scheduled_for=scheduled_for,
            expires_at=expires_at,
            history=(WorkHistoryEvent(state=WorkState.PENDING, recorded_at=scheduled_for),),
        )

    def transition(
        self, state: WorkState, *, now: datetime, reason: str | None = None
    ) -> "QueuedWorkItem":
        return self.model_copy(
            update={
                "state": state,
                "history": (*self.history, WorkHistoryEvent(state=state, recorded_at=now, reason=reason)),
            }
        )
