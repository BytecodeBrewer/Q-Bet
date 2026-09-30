"""Durable, mode-isolated work queues for controlled workflow dispatch."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from qbet.domain.models import DomainModel, Identifier
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.request_handler.models import TargetedMarketRevalidationContext
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
    market_revalidation: TargetedMarketRevalidationContext | None = None
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
        if isinstance(self.request, BonusEngineRequest):
            dependency = self.request.bonus_offer_dependency
            context = self.market_revalidation
            if dependency is not None and context is not None:
                if (
                    dependency.sport != context.sport
                    or dependency.event_id != context.event_id
                    or dependency.market != context.market
                ):
                    raise ValueError(
                        "bonus dependency and market revalidation identity must match"
                    )
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
        market_revalidation: TargetedMarketRevalidationContext | None = None,
    ) -> "QueuedWorkItem":
        return cls(
            work=work,
            request=request,
            market_revalidation=market_revalidation,
            scheduled_for=scheduled_for,
            expires_at=expires_at,
            history=(WorkHistoryEvent(state=WorkState.PENDING, recorded_at=scheduled_for),),
        )

    def transition(
        self, state: WorkState, *, now: datetime, reason: str | None = None
    ) -> "QueuedWorkItem":
        permitted = {
            WorkState.PENDING: {
                WorkState.PROCESSING,
                WorkState.RECHECK,
                WorkState.EXPIRED,
                WorkState.CANCELLED,
            },
            WorkState.PROCESSING: {
                WorkState.RECHECK,
                WorkState.COMPLETED,
                WorkState.CANCELLED,
                WorkState.FAILED,
                WorkState.EXPIRED,
            },
            WorkState.RECHECK: {
                WorkState.PENDING,
                WorkState.COMPLETED,
                WorkState.CANCELLED,
                WorkState.FAILED,
                WorkState.EXPIRED,
            },
        }
        if state not in permitted.get(self.state, set()):
            raise ValueError(f"invalid work transition: {self.state} -> {state}")
        return self.model_copy(
            update={
                "state": state,
                "history": (*self.history, WorkHistoryEvent(state=state, recorded_at=now, reason=reason)),
            }
        )

    def reschedule(self, *, scheduled_for: datetime, now: datetime) -> "QueuedWorkItem":
        if self.state not in {WorkState.PENDING, WorkState.RECHECK}:
            raise ValueError("only pending or recheck work can be rescheduled")
        if scheduled_for >= self.expires_at:
            raise ValueError("rescheduled work must remain before expires_at")
        if self.state is WorkState.RECHECK:
            pending = self.transition(WorkState.PENDING, now=now, reason="rescheduled")
            return pending.model_copy(update={"scheduled_for": scheduled_for})
        return self.model_copy(
            update={
                "scheduled_for": scheduled_for,
                "history": (
                    *self.history,
                    WorkHistoryEvent(
                        state=WorkState.PENDING, recorded_at=now, reason="rescheduled"
                    ),
                ),
            }
        )
