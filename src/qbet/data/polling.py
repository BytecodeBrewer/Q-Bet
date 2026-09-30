"""Deterministic, provider-aware scheduling policy for market and result refreshes."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from qbet.data.models import DataSourceMetadata, SourceTransport
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
    DISABLED = "disabled"
    DEFERRED_CAPACITY = "deferred_capacity"


class PollingCapacityClass(StrEnum):
    FREE = "free"
    TEST = "test"
    PAID = "paid"


class PollingRequest(DomainModel):
    """One provider-neutral refresh target with explicit timing and identity."""

    source: DataSourceMetadata
    target: PollingTarget
    match_id: Identifier
    correlation_id: UUID
    fetched_at: AwareDatetime | None
    event_starts_at: AwareDatetime
    next_poll_at: AwareDatetime
    attempt: int = Field(ge=0)
    result_available: bool = False
    result_partial: bool = False
    terminal: bool = False
    result_tracking_required: bool = True
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
    """Legacy-compatible default policy used when no persisted strategy resolver is supplied."""

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


class PollingStrategy(DomainModel):
    """Persistable provider/target policy with optional engine-specific override."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    source: DataSourceMetadata
    target: PollingTarget
    engine: Literal["bonus", "sports_capital"] | None = None
    enabled: bool = True
    freshness_window: timedelta = timedelta(minutes=5)
    market_refresh_points: tuple[timedelta, ...] = ()
    market_interval: timedelta | None = None
    latest_market_poll_before_event: timedelta = timedelta(minutes=1)
    result_retry_interval: timedelta = timedelta(minutes=10)
    max_attempts: int = Field(default=3, ge=1)
    capacity_class: PollingCapacityClass = PollingCapacityClass.FREE
    capacity_units: int | None = Field(default=None, ge=0)
    request_cost_units: int = Field(default=1, ge=1)

    @property
    def key(self) -> tuple[str, str, PollingTarget, str | None]:
        return (
            self.source.provider_id,
            self.source.source_id,
            self.target,
            self.engine,
        )

    @model_validator(mode="after")
    def validates_strategy(self) -> "PollingStrategy":
        if self.source.transport is SourceTransport.WEB:
            raise ValueError("Smart Polling requires an API or in-memory test source")
        if self.freshness_window <= timedelta():
            raise ValueError("polling freshness_window must be positive")
        if self.result_retry_interval <= timedelta():
            raise ValueError("polling result_retry_interval must be positive")
        if self.latest_market_poll_before_event <= timedelta():
            raise ValueError("polling latest_market_poll_before_event must be positive")
        if self.market_interval is not None and self.market_interval <= timedelta():
            raise ValueError("polling market_interval must be positive when configured")
        if self.market_refresh_points and self.market_interval is not None:
            raise ValueError("configure market refresh points or a fallback interval, not both")
        if any(point <= timedelta() for point in self.market_refresh_points):
            raise ValueError("polling market refresh points must be positive")
        if len(set(self.market_refresh_points)) != len(self.market_refresh_points):
            raise ValueError("polling market refresh points must be unique")
        if any(
            point < self.latest_market_poll_before_event
            for point in self.market_refresh_points
        ):
            raise ValueError("polling market refresh points cross the latest polling boundary")
        if any(
            earlier <= later
            for earlier, later in zip(
                self.market_refresh_points,
                self.market_refresh_points[1:],
                strict=False,
            )
        ):
            raise ValueError("polling market refresh points must be ordered far-to-near")
        if self.target is PollingTarget.MARKET:
            if not self.market_refresh_points and self.market_interval is None:
                raise ValueError(
                    "market polling strategy requires refresh points or a fallback interval"
                )
        elif self.market_refresh_points:
            raise ValueError("result polling strategy cannot define market refresh points")
        return self

    @classmethod
    def from_configuration(
        cls,
        *,
        source: DataSourceMetadata,
        target: PollingTarget,
        configuration: SmartPollingConfiguration,
    ) -> "PollingStrategy":
        return cls(
            source=source,
            target=target,
            freshness_window=configuration.market_freshness_window,
            market_interval=(
                configuration.market_interval if target is PollingTarget.MARKET else None
            ),
            latest_market_poll_before_event=configuration.latest_market_poll_before_event,
            result_retry_interval=configuration.result_retry_interval,
            max_attempts=configuration.max_attempts,
        )


class PollingStrategyResolutionError(ValueError):
    """Stable strategy-resolution failure for missing or ambiguous configuration."""

    def __init__(self, reason_code: Identifier) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class PollingStrategyResolver:
    """Resolve exact engine overrides before provider/target defaults."""

    def __init__(self, strategies: tuple[PollingStrategy, ...]) -> None:
        self._strategies = strategies

    def resolve(self, request: PollingRequest) -> PollingStrategy:
        candidates = tuple(
            strategy
            for strategy in self._strategies
            if strategy.source == request.source
            and strategy.target is request.target
            and (strategy.engine is None or strategy.engine == request.engine)
        )
        if not candidates:
            raise PollingStrategyResolutionError("polling_strategy_unavailable")

        exact = tuple(strategy for strategy in candidates if strategy.engine == request.engine)
        selected = exact or tuple(strategy for strategy in candidates if strategy.engine is None)
        if len(selected) != 1:
            raise PollingStrategyResolutionError("ambiguous_polling_strategy")
        return selected[0]


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
        self,
        configuration: SmartPollingConfiguration | None = None,
        *,
        resolver: PollingStrategyResolver | None = None,
    ) -> None:
        if configuration is not None and resolver is not None:
            raise ValueError("configure SmartPollingPolicy with defaults or a strategy resolver, not both")
        self._configuration = configuration or SmartPollingConfiguration()
        self._resolver = resolver

    def decide(self, request: PollingRequest) -> PollingDecision:
        if request.terminal or (
            request.target is PollingTarget.RESULT and request.result_available
        ):
            return PollingDecision(
                request=request, outcome=PollingOutcome.TERMINAL, reason="terminal_result"
            )
        if request.target is PollingTarget.RESULT and not request.result_tracking_required:
            return PollingDecision(
                request=request,
                outcome=PollingOutcome.TERMINAL,
                reason="result_tracking_not_required",
            )

        strategy = self._strategy_for(request)
        if not strategy.enabled:
            return PollingDecision(
                request=request,
                outcome=PollingOutcome.DISABLED,
                reason="polling_strategy_disabled",
            )
        if request.attempt >= strategy.max_attempts:
            return PollingDecision(
                request=request,
                outcome=PollingOutcome.RETRY_EXHAUSTED,
                reason="retry_attempts_exhausted",
            )
        if request.target is PollingTarget.MARKET:
            return self._market_decision(request, strategy)
        return self._result_decision(request, strategy)

    def _strategy_for(self, request: PollingRequest) -> PollingStrategy:
        if self._resolver is not None:
            return self._resolver.resolve(request)
        return PollingStrategy.from_configuration(
            source=request.source,
            target=request.target,
            configuration=self._configuration,
        )

    @staticmethod
    def _capacity_decision(
        request: PollingRequest,
        strategy: PollingStrategy,
        *,
        freshness_deadline: AwareDatetime | None = None,
    ) -> PollingDecision | None:
        if strategy.capacity_units is None or strategy.capacity_units >= strategy.request_cost_units:
            return None
        return PollingDecision(
            request=request,
            outcome=PollingOutcome.DEFERRED_CAPACITY,
            reason="polling_capacity_insufficient",
            freshness_deadline=freshness_deadline,
        )

    def _market_decision(
        self, request: PollingRequest, strategy: PollingStrategy
    ) -> PollingDecision:
        freshness_deadline = (
            request.fetched_at + strategy.freshness_window
            if request.fetched_at is not None
            else None
        )
        if freshness_deadline is not None and freshness_deadline > request.next_poll_at:
            return PollingDecision(
                request=request,
                outcome=PollingOutcome.SKIPPED_FRESH,
                reason="market_data_fresh",
                freshness_deadline=freshness_deadline,
            )

        scheduled_for = self._market_schedule(request, strategy)
        if scheduled_for is None:
            return PollingDecision(
                request=request,
                outcome=PollingOutcome.EXPIRED,
                reason="market_event_window_closed",
                freshness_deadline=freshness_deadline,
            )

        capacity = self._capacity_decision(
            request,
            strategy,
            freshness_deadline=freshness_deadline,
        )
        if capacity is not None:
            return capacity
        return PollingDecision(
            request=request,
            outcome=PollingOutcome.SCHEDULED,
            reason=(
                "market_refresh_point_due"
                if strategy.market_refresh_points
                else "market_refresh_due"
            ),
            scheduled_for=scheduled_for,
            freshness_deadline=freshness_deadline,
        )

    @staticmethod
    def _market_schedule(
        request: PollingRequest, strategy: PollingStrategy
    ) -> AwareDatetime | None:
        if strategy.market_refresh_points:
            for point in strategy.market_refresh_points:
                candidate = request.event_starts_at - point
                if candidate >= request.next_poll_at:
                    return candidate
            return None

        interval = strategy.market_interval
        assert interval is not None
        latest = request.event_starts_at - strategy.latest_market_poll_before_event
        if request.next_poll_at >= latest:
            return None
        return min(request.next_poll_at + interval, latest)

    def _result_decision(
        self, request: PollingRequest, strategy: PollingStrategy
    ) -> PollingDecision:
        if request.next_poll_at < request.event_starts_at:
            return PollingDecision(
                request=request,
                outcome=PollingOutcome.INVALID,
                reason="result_before_event",
            )
        capacity = self._capacity_decision(request, strategy)
        if capacity is not None:
            return capacity
        return PollingDecision(
            request=request,
            outcome=PollingOutcome.SCHEDULED,
            reason="partial_result_retry" if request.result_partial else "result_retry_due",
            scheduled_for=request.next_poll_at + strategy.result_retry_interval,
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
