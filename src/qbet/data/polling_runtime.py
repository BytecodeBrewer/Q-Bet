"""Bounded runtime execution for durable Smart Polling work."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from enum import StrEnum
from time import perf_counter
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, Field

from qbet.data.models import (
    DataCollectionRequest,
    DataSourceMetadata,
    DataTarget,
    NormalizedMarketSnapshot,
)
from qbet.data.polling import (
    PollingOutcome,
    PollingRequest,
    PollingStrategyResolutionError,
    PollingTarget,
    SmartPollingPolicy,
)
from qbet.data.the_odds_api import (
    TheOddsApiAuthenticationError,
    TheOddsApiConfigurationError,
    TheOddsApiError,
    TheOddsApiPayloadError,
    TheOddsApiRateLimitError,
    TheOddsApiTransportError,
)
from qbet.domain.models import DomainModel, Identifier
from qbet.monitoring import MonitoringLevel, MonitoringRecord


class PollingRuntimeOutcome(StrEnum):
    SCHEDULED = "scheduled"
    SKIPPED_FRESH = "skipped_fresh"
    SUCCESS = "success"
    DELAYED = "delayed"
    UNAVAILABLE = "unavailable"
    PROVIDER_ERROR = "provider_error"
    CONFIGURATION_ERROR = "configuration_error"
    TERMINAL = "terminal"
    DISABLED = "disabled"


class PollingWork(DomainModel):
    """Restart-safe polling state for one owner/engine/mode/market identity."""

    owner: Identifier
    source: DataSourceMetadata
    target: PollingTarget
    engine: Literal["bonus", "sports_capital"]
    mode: Literal["simulation", "execution"]
    match_id: Identifier
    correlation_id: UUID
    sport: Identifier
    market: Identifier
    event_starts_at: AwareDatetime
    next_due_at: AwareDatetime
    attempt: int = Field(default=0, ge=0)
    last_outcome: Identifier | None = None
    last_reason: Identifier | None = None
    last_success_at: AwareDatetime | None = None
    terminal: bool = False
    disabled: bool = False
    latest_snapshot: NormalizedMarketSnapshot | None = None

    @property
    def identity(self) -> tuple[str, str, str, PollingTarget, str, str, str]:
        return (
            self.owner,
            self.source.provider_id,
            self.source.source_id,
            self.target,
            self.engine,
            self.mode,
            self.match_id,
        )

    def request(self, *, evaluation_at: AwareDatetime | None = None) -> PollingRequest:
        return PollingRequest(
            source=self.source,
            target=self.target,
            match_id=self.match_id,
            correlation_id=self.correlation_id,
            fetched_at=self.last_success_at,
            event_starts_at=self.event_starts_at,
            next_poll_at=evaluation_at or self.next_due_at,
            attempt=self.attempt,
            mode=self.mode,
            engine=self.engine,
        )

    def collection_request(self) -> DataCollectionRequest:
        return DataCollectionRequest(
            correlation_id=self.correlation_id,
            target=DataTarget(self.engine),
            source=self.source,
            sport=self.sport,
            event_id=self.match_id,
            market=self.market,
        )


class PollingWorkRepository(Protocol):
    def synchronize(self, active_work: Iterable[PollingWork]) -> tuple[PollingWork, ...]: ...

    def claim_due(
        self,
        *,
        now: AwareDatetime,
        limit: int,
        lease_for: timedelta,
    ) -> tuple[PollingWork, ...]: ...

    def save(self, work: PollingWork) -> PollingWork: ...


class PollingMarketCollector(Protocol):
    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot: ...


class PollingMonitoringWriter(Protocol):
    def append(self, record: MonitoringRecord) -> MonitoringRecord: ...


class PollingTickResult(DomainModel):
    processed: int = Field(ge=0)
    provider_requests: int = Field(ge=0)
    outcomes: tuple[PollingRuntimeOutcome, ...] = ()


class SmartPollingRuntime:
    """Execute one bounded polling tick while leaving scheduling policy authoritative."""

    def __init__(
        self,
        *,
        repository: PollingWorkRepository,
        policy: SmartPollingPolicy,
        collector: PollingMarketCollector,
        monitoring_writer: PollingMonitoringWriter,
        defer_interval: timedelta = timedelta(minutes=5),
    ) -> None:
        if defer_interval <= timedelta():
            raise ValueError("defer_interval must be positive")
        self._repository = repository
        self._policy = policy
        self._collector = collector
        self._monitoring_writer = monitoring_writer
        self._defer_interval = defer_interval

    def tick(
        self,
        *,
        active_work: Iterable[PollingWork],
        now: AwareDatetime,
        limit: int,
        lease_for: timedelta,
    ) -> PollingTickResult:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if lease_for <= timedelta():
            raise ValueError("lease_for must be positive")

        self._repository.synchronize(active_work)
        claimed = self._repository.claim_due(now=now, limit=limit, lease_for=lease_for)
        outcomes: list[PollingRuntimeOutcome] = []
        provider_requests = 0
        for work in claimed:
            outcome, dispatched = self._process(work, now=now)
            outcomes.append(outcome)
            provider_requests += int(dispatched)
        return PollingTickResult(
            processed=len(claimed),
            provider_requests=provider_requests,
            outcomes=tuple(outcomes),
        )

    def _process(
        self,
        work: PollingWork,
        *,
        now: AwareDatetime,
    ) -> tuple[PollingRuntimeOutcome, bool]:
        try:
            decision = self._policy.decide(work.request())
            if (
                decision.outcome is PollingOutcome.SKIPPED_FRESH
                and decision.freshness_deadline is not None
                and decision.freshness_deadline <= now
            ):
                decision = self._policy.decide(work.request(evaluation_at=now))
        except PollingStrategyResolutionError as error:
            return self._persist_non_success(
                work,
                now=now,
                outcome=PollingRuntimeOutcome.CONFIGURATION_ERROR,
                reason=error.reason_code,
                next_due_at=now + self._defer_interval,
                level=MonitoringLevel.ERROR,
            ), False

        if decision.outcome is PollingOutcome.SCHEDULED:
            assert decision.scheduled_for is not None
            if decision.scheduled_for > now:
                updated = work.model_copy(
                    update={
                        "next_due_at": decision.scheduled_for,
                        "last_outcome": PollingRuntimeOutcome.SCHEDULED.value,
                        "last_reason": decision.reason,
                    }
                )
                self._repository.save(updated)
                self._record(
                    updated,
                    now=now,
                    status=PollingRuntimeOutcome.SCHEDULED.value,
                    reason=decision.reason,
                )
                return PollingRuntimeOutcome.SCHEDULED, False
            return self._fetch(work, now=now)

        if decision.outcome is PollingOutcome.SKIPPED_FRESH:
            next_due = decision.freshness_deadline or now + self._defer_interval
            updated = work.model_copy(
                update={
                    "next_due_at": max(next_due, now),
                    "last_outcome": PollingRuntimeOutcome.SKIPPED_FRESH.value,
                    "last_reason": decision.reason,
                }
            )
            self._repository.save(updated)
            self._record(
                updated,
                now=now,
                status=PollingRuntimeOutcome.SKIPPED_FRESH.value,
                reason=decision.reason,
            )
            return PollingRuntimeOutcome.SKIPPED_FRESH, False

        if decision.outcome is PollingOutcome.DEFERRED_CAPACITY:
            return self._persist_non_success(
                work,
                now=now,
                outcome=PollingRuntimeOutcome.DELAYED,
                reason=decision.reason,
                next_due_at=now + self._defer_interval,
                level=MonitoringLevel.WARNING,
            ), False

        if decision.outcome is PollingOutcome.DISABLED:
            updated = work.model_copy(
                update={
                    "disabled": True,
                    "last_outcome": PollingRuntimeOutcome.DISABLED.value,
                    "last_reason": decision.reason,
                }
            )
            self._repository.save(updated)
            self._record(
                updated,
                now=now,
                status=PollingRuntimeOutcome.DISABLED.value,
                reason=decision.reason,
            )
            return PollingRuntimeOutcome.DISABLED, False

        if decision.outcome in {
            PollingOutcome.TERMINAL,
            PollingOutcome.EXPIRED,
            PollingOutcome.INVALID,
            PollingOutcome.RETRY_EXHAUSTED,
        }:
            updated = work.model_copy(
                update={
                    "terminal": True,
                    "last_outcome": PollingRuntimeOutcome.TERMINAL.value,
                    "last_reason": decision.reason,
                }
            )
            self._repository.save(updated)
            self._record(
                updated,
                now=now,
                status=PollingRuntimeOutcome.TERMINAL.value,
                reason=decision.reason,
            )
            return PollingRuntimeOutcome.TERMINAL, False

        return self._persist_non_success(
            work,
            now=now,
            outcome=PollingRuntimeOutcome.CONFIGURATION_ERROR,
            reason="polling_decision_unsupported",
            next_due_at=now + self._defer_interval,
            level=MonitoringLevel.ERROR,
        ), False

    def _fetch(
        self,
        work: PollingWork,
        *,
        now: AwareDatetime,
    ) -> tuple[PollingRuntimeOutcome, bool]:
        self._record(work, now=now, status="fetching", reason=None)
        started = perf_counter()
        try:
            snapshot = self._collector.collect(work.collection_request())
        except TheOddsApiRateLimitError:
            return self._provider_failure(
                work,
                now=now,
                outcome=PollingRuntimeOutcome.DELAYED,
                reason="polling_provider_rate_limited",
                level=MonitoringLevel.WARNING,
                started=started,
            ), True
        except TheOddsApiAuthenticationError:
            return self._provider_failure(
                work,
                now=now,
                outcome=PollingRuntimeOutcome.PROVIDER_ERROR,
                reason="polling_provider_auth_failed",
                level=MonitoringLevel.ERROR,
                started=started,
            ), True
        except TheOddsApiConfigurationError:
            return self._provider_failure(
                work,
                now=now,
                outcome=PollingRuntimeOutcome.CONFIGURATION_ERROR,
                reason="polling_provider_configuration_invalid",
                level=MonitoringLevel.ERROR,
                started=started,
            ), True
        except TheOddsApiPayloadError:
            return self._provider_failure(
                work,
                now=now,
                outcome=PollingRuntimeOutcome.PROVIDER_ERROR,
                reason="polling_provider_payload_invalid",
                level=MonitoringLevel.ERROR,
                started=started,
            ), True
        except TheOddsApiTransportError:
            return self._provider_failure(
                work,
                now=now,
                outcome=PollingRuntimeOutcome.UNAVAILABLE,
                reason="polling_provider_unavailable",
                level=MonitoringLevel.ERROR,
                started=started,
            ), True
        except TheOddsApiError:
            return self._provider_failure(
                work,
                now=now,
                outcome=PollingRuntimeOutcome.UNAVAILABLE,
                reason="polling_provider_unavailable",
                level=MonitoringLevel.ERROR,
                started=started,
            ), True

        updated = work.model_copy(
            update={
                "attempt": 0,
                "last_outcome": PollingRuntimeOutcome.SUCCESS.value,
                "last_reason": "polling_provider_fetch_success",
                "last_success_at": snapshot.fetched_at,
                "latest_snapshot": snapshot,
            }
        )
        updated = self._schedule_after_success(updated, now=now)
        self._repository.save(updated)
        self._record(
            updated,
            now=now,
            status=PollingRuntimeOutcome.SUCCESS.value,
            reason="polling_provider_fetch_success",
            duration_ms=_elapsed_ms(started),
        )
        return PollingRuntimeOutcome.SUCCESS, True

    def _schedule_after_success(self, work: PollingWork, *, now: AwareDatetime) -> PollingWork:
        decision = self._policy.decide(work.request(evaluation_at=now))
        if decision.outcome is PollingOutcome.SKIPPED_FRESH:
            return work.model_copy(
                update={"next_due_at": decision.freshness_deadline or now + self._defer_interval}
            )
        if decision.outcome is PollingOutcome.SCHEDULED and decision.scheduled_for is not None:
            return work.model_copy(update={"next_due_at": decision.scheduled_for})
        if decision.outcome is PollingOutcome.DISABLED:
            return work.model_copy(update={"disabled": True})
        if decision.outcome in {
            PollingOutcome.TERMINAL,
            PollingOutcome.EXPIRED,
            PollingOutcome.INVALID,
            PollingOutcome.RETRY_EXHAUSTED,
        }:
            return work.model_copy(update={"terminal": True})
        return work.model_copy(update={"next_due_at": now + self._defer_interval})

    def _provider_failure(
        self,
        work: PollingWork,
        *,
        now: AwareDatetime,
        outcome: PollingRuntimeOutcome,
        reason: Identifier,
        level: MonitoringLevel,
        started: float,
    ) -> PollingRuntimeOutcome:
        self._persist_non_success(
            work,
            now=now,
            outcome=outcome,
            reason=reason,
            next_due_at=now + self._defer_interval,
            level=level,
            duration_ms=_elapsed_ms(started),
            increment_attempt=True,
        )
        return outcome

    def _persist_non_success(
        self,
        work: PollingWork,
        *,
        now: AwareDatetime,
        outcome: PollingRuntimeOutcome,
        reason: Identifier,
        next_due_at: AwareDatetime,
        level: MonitoringLevel,
        duration_ms: int | None = None,
        increment_attempt: bool = False,
    ) -> PollingRuntimeOutcome:
        updated = work.model_copy(
            update={
                "attempt": work.attempt + int(increment_attempt),
                "next_due_at": next_due_at,
                "last_outcome": outcome.value,
                "last_reason": reason,
            }
        )
        self._repository.save(updated)
        self._record(
            updated,
            now=now,
            status=outcome.value,
            reason=reason,
            level=level,
            duration_ms=duration_ms,
        )
        return outcome

    def _record(
        self,
        work: PollingWork,
        *,
        now: AwareDatetime,
        status: Identifier,
        reason: Identifier | None,
        level: MonitoringLevel = MonitoringLevel.INFO,
        duration_ms: int | None = None,
    ) -> None:
        try:
            self._monitoring_writer.append(
                MonitoringRecord(
                    correlation_id=work.correlation_id,
                    occurred_at=now,
                    engine=work.engine,
                    mode=work.mode,
                    stage="data_aggregation",
                    event_type="polling",
                    status=status,
                    reason_code=reason,
                    level=level,
                    duration_ms=duration_ms,
                    references={
                        "provider_id": work.source.provider_id,
                        "source_id": work.source.source_id,
                        "target": work.target.value,
                        "match_id": work.match_id,
                    },
                )
            )
        except OSError:
            return


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))
