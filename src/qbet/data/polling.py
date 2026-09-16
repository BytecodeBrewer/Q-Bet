"""Deterministic, provider-neutral scheduling policy for market and result refreshes."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from qbet.data.models import DataSourceMetadata
from qbet.domain.models import DomainModel, Identifier


class PollingTarget(StrEnum):
    MARKET = "market"
    RESULT = "result"


class PollingOutcome(StrEnum):
    SCHEDULED = "scheduled"
    SKIPPED_FRESH = "skipped_fresh"
    TERMINAL = "terminal"
    EXPIRED = "expired"
    INVALID = "invalid"
    RETRY_EXHAUSTED = "retry_exhausted"
    DUPLICATE = "duplicate"


class PollingRequest(DomainModel):
    """One provider-neutral refresh target with explicit timing and identity."""

    source: DataSourceMetadata
    target: PollingTarget
    match_id: Identifier
    correlation_id: UUID
    fetched_at: AwareDatetime
    event_starts_at: AwareDatetime
    next_poll_at: AwareDatetime
    attempt: int = Field(ge=0)
    result_available: bool = False
    result_partial: bool = False
    terminal: bool = False
    mode: Identifier
    engine: Identifier

    @property
    def key(self) -> tuple[PollingTarget, str, str, str, UUID, str, str]:
        return (
            self.target,
            self.source.provider_id,
            self.source.source_id,
            self.match_id,
            self.correlation_id,
            self.mode,
            self.engine,
        )


class SmartPollingConfiguration(DomainModel):
    """Bounded deterministic intervals supplied by application configuration."""

    market_interval: timedelta = timedelta(minutes=5)
    market_freshness_window: timedelta = timedelta(minutes=5)
    result_retry_interval: timedelta = timedelta(minutes=10)
    max_attempts: int = Field(default=3, ge=1)
    latest_market_poll_before_event: timedelta = timedelta(minutes=1)

    @model_validator(mode="after")
    def validates_intervals(self) -> "SmartPollingConfiguration":
        if (
            min(
                self.market_interval,
                self.market_freshness_window,
                self.result_retry_interval,
                self.latest_market_poll_before_event,
            )
            <= timedelta()
        ):
            raise ValueError("polling intervals must be positive")
        return self


class PollingDecision(DomainModel):
    """Typed policy result that can be queued without dispatching a collector."""

    request: PollingRequest
    outcome: PollingOutcome
    reason: Identifier
    scheduled_for: AwareDatetime | None = None
    freshness_deadline: AwareDatetime | None = None

    @model_validator(mode="after")
    def validates_schedule(self) -> "PollingDecision":
        if self.outcome is PollingOutcome.SCHEDULED and self.scheduled_for is None:
            raise ValueError("scheduled decisions require scheduled_for")
        if self.outcome is not PollingOutcome.SCHEDULED and self.scheduled_for is not None:
            raise ValueError("non-scheduled decisions must not include scheduled_for")
        return self


class SmartPollingPolicy:
    """Pure policy with no wall clock, transport, calculation, or execution access."""

    def __init__(
        self, configuration: SmartPollingConfiguration = SmartPollingConfiguration()
    ) -> None:
        self._configuration = configuration

    def decide(self, request: PollingRequest) -> PollingDecision:
        if request.terminal or (
            request.target is PollingTarget.RESULT and request.result_available
        ):
            return PollingDecision(
                request=request, outcome=PollingOutcome.TERMINAL, reason="terminal_result"
            )
        if request.attempt >= self._configuration.max_attempts:
            return PollingDecision(
                request=request,
                outcome=PollingOutcome.RETRY_EXHAUSTED,
                reason="retry_attempts_exhausted",
            )
        if request.target is PollingTarget.MARKET:
            return self._market_decision(request)
        return self._result_decision(request)

    def _market_decision(self, request: PollingRequest) -> PollingDecision:
        freshness_deadline = request.fetched_at + self._configuration.market_freshness_window
        if freshness_deadline > request.next_poll_at:
            return PollingDecision(
                request=request,
                outcome=PollingOutcome.SKIPPED_FRESH,
                reason="market_data_fresh",
                freshness_deadline=freshness_deadline,
            )
        latest = request.event_starts_at - self._configuration.latest_market_poll_before_event
        if request.next_poll_at >= latest:
            return PollingDecision(
                request=request,
                outcome=PollingOutcome.EXPIRED,
                reason="market_event_window_closed",
                freshness_deadline=freshness_deadline,
            )
        return PollingDecision(
            request=request,
            outcome=PollingOutcome.SCHEDULED,
            reason="market_refresh_due",
            scheduled_for=min(request.next_poll_at + self._configuration.market_interval, latest),
            freshness_deadline=freshness_deadline,
        )

    def _result_decision(self, request: PollingRequest) -> PollingDecision:
        if request.next_poll_at < request.event_starts_at:
            return PollingDecision(
                request=request,
                outcome=PollingOutcome.INVALID,
                reason="result_before_event",
            )
        return PollingDecision(
            request=request,
            outcome=PollingOutcome.SCHEDULED,
            reason="partial_result_retry" if request.result_partial else "result_retry_due",
            scheduled_for=request.next_poll_at + self._configuration.result_retry_interval,
        )


class PollingSchedule:
    """Small application queue boundary that never dispatches collectors itself."""

    def __init__(self) -> None:
        self._entries: dict[
            tuple[PollingTarget, str, str, str, UUID, str, str], PollingDecision
        ] = {}

    def schedule(self, decision: PollingDecision) -> PollingDecision:
        if decision.outcome is not PollingOutcome.SCHEDULED:
            return decision
        existing = self._entries.get(decision.request.key)
        if existing is not None and existing.request.next_poll_at == decision.request.next_poll_at:
            return PollingDecision(
                request=decision.request,
                outcome=PollingOutcome.DUPLICATE,
                reason="polling_request_already_scheduled",
            )
        self._entries[decision.request.key] = decision
        return decision

    def complete(self, request: PollingRequest) -> None:
        self._entries.pop(request.key, None)

    def entries(self) -> tuple[PollingDecision, ...]:
        return tuple(self._entries.values())
