"""Bounded hosted runtime composition for provider-aware Smart Polling."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol, cast

from qbet.data.models import DataCollectionRequest, DataTarget, NormalizedMarketSnapshot
from qbet.data.polling import (
    PollingOutcome,
    PollingStrategy,
    PollingStrategyResolutionError,
    PollingStrategyResolver,
    PollingTarget,
    SmartPollingPolicy,
)
from qbet.data.polling_work import PollingMarketSelection, PollingWorkItem, PollingWorkState
from qbet.data.the_odds_api import (
    TheOddsApiAuthenticationError,
    TheOddsApiConfigurationError,
    TheOddsApiError,
    TheOddsApiPayloadError,
    TheOddsApiRateLimitError,
    TheOddsApiTransportError,
)
from qbet.monitoring import MonitoringLevel, MonitoringRecord
from qbet.workflow.routing import (
    RoutingConfiguration,
    UserRoutingPreferences,
    V1Engine,
    V1_ENGINES,
    effective_engine_modes,
)


class PollingWorkStore(Protocol):
    def ensure(self, work: PollingWorkItem) -> PollingWorkItem: ...

    def save(self, work: PollingWorkItem) -> PollingWorkItem: ...

    def claim_due(
        self, *, now: datetime, limit: int
    ) -> tuple[PollingWorkItem, ...]: ...


class PollingStrategyStore(Protocol):
    def list(self) -> tuple[PollingStrategy, ...]: ...


class PollingMarketCollector(Protocol):
    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot: ...


class PollingMonitoringWriter(Protocol):
    def append(self, record: MonitoringRecord) -> MonitoringRecord: ...


@dataclass(frozen=True)
class PollingTickSummary:
    eligible_routes: int = 0
    claimed: int = 0
    provider_calls: int = 0
    successes: int = 0
    deferred: int = 0
    failures: int = 0
    terminal: int = 0


class SmartPollingRuntime:
    """Compose durable work, pure polling policy, routing, provider I/O, and activity."""

    def __init__(
        self,
        *,
        work_store: PollingWorkStore,
        strategy_store: PollingStrategyStore,
        collectors: Mapping[str, PollingMarketCollector],
        monitoring_writer: PollingMonitoringWriter | None = None,
    ) -> None:
        self._work_store = work_store
        self._strategy_store = strategy_store
        self._collectors = collectors
        self._monitoring_writer = monitoring_writer

    def tick(
        self,
        *,
        selection: PollingMarketSelection,
        routing: RoutingConfiguration,
        preferences: tuple[tuple[str, UserRoutingPreferences], ...],
        now: datetime,
        max_work: int,
    ) -> PollingTickSummary:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("polling tick time must include timezone information")
        if max_work < 1:
            raise ValueError("polling tick max_work must be positive")

        strategies = self._strategy_store.list()
        resolver = PollingStrategyResolver(strategies)
        policy = SmartPollingPolicy(resolver=resolver)
        eligible_routes = self._seed_eligible_work(
            selection=selection,
            routing=routing,
            preferences=preferences,
            strategies=strategies,
            resolver=resolver,
            now=now,
        )
        claimed = self._work_store.claim_due(now=now, limit=max_work)

        provider_calls = successes = deferred = failures = terminal = 0
        preference_map = dict(preferences)
        for work in claimed:
            if not self._route_is_active(work, routing, preference_map):
                self._disable(work, now=now, reason="polling_route_disabled")
                terminal += 1
                continue

            try:
                decision = policy.decide(work.request)
            except PollingStrategyResolutionError:
                self._retry_failure(
                    work,
                    now=now,
                    outcome="provider_error",
                    reason="polling_strategy_unavailable",
                    level=MonitoringLevel.ERROR,
                )
                failures += 1
                continue

            if decision.outcome is PollingOutcome.SCHEDULED:
                assert decision.scheduled_for is not None
                if decision.scheduled_for > now:
                    scheduled = work.model_copy(
                        update={
                            "state": PollingWorkState.PENDING,
                            "claimed_at": None,
                            "next_due_at": decision.scheduled_for,
                            "last_outcome": PollingOutcome.SCHEDULED.value,
                            "last_reason": decision.reason,
                        }
                    )
                    self._work_store.save(scheduled)
                    self._record(
                        scheduled,
                        now=now,
                        event_type="polling_schedule",
                        status="scheduled",
                        reason=decision.reason,
                        level=MonitoringLevel.INFO,
                    )
                    deferred += 1
                    continue
                provider_calls += 1
                if self._fetch(work, resolver=resolver, now=now):
                    successes += 1
                else:
                    failures += 1
                continue

            if decision.outcome is PollingOutcome.SKIPPED_FRESH:
                assert decision.freshness_deadline is not None
                request = work.request.model_copy(
                    update={"next_poll_at": decision.freshness_deadline}
                )
                skipped = work.model_copy(
                    update={
                        "request": request,
                        "state": PollingWorkState.PENDING,
                        "claimed_at": None,
                        "next_due_at": decision.freshness_deadline,
                        "last_outcome": PollingOutcome.SKIPPED_FRESH.value,
                        "last_reason": decision.reason,
                    }
                )
                self._work_store.save(skipped)
                self._record(
                    skipped,
                    now=now,
                    event_type="polling_schedule",
                    status="skipped_fresh",
                    reason=decision.reason,
                    level=MonitoringLevel.INFO,
                )
                deferred += 1
                continue

            if decision.outcome is PollingOutcome.DEFERRED_CAPACITY:
                deferred_work = work.model_copy(
                    update={
                        "state": PollingWorkState.PENDING,
                        "claimed_at": None,
                        "next_due_at": now,
                        "last_outcome": PollingOutcome.DEFERRED_CAPACITY.value,
                        "last_reason": decision.reason,
                    }
                )
                self._work_store.save(deferred_work)
                self._record(
                    deferred_work,
                    now=now,
                    event_type="polling_schedule",
                    status="deferred",
                    reason=decision.reason,
                    level=MonitoringLevel.WARNING,
                )
                deferred += 1
                continue

            if decision.outcome is PollingOutcome.DISABLED:
                self._disable(work, now=now, reason=decision.reason)
                terminal += 1
                continue

            terminal_work = work.model_copy(
                update={
                    "state": PollingWorkState.TERMINAL,
                    "claimed_at": None,
                    "next_due_at": now,
                    "last_outcome": decision.outcome.value,
                    "last_reason": decision.reason,
                }
            )
            self._work_store.save(terminal_work)
            self._record(
                terminal_work,
                now=now,
                event_type="polling_schedule",
                status="terminal",
                reason=decision.reason,
                level=(
                    MonitoringLevel.WARNING
                    if decision.outcome
                    in {
                        PollingOutcome.EXPIRED,
                        PollingOutcome.INVALID,
                        PollingOutcome.RETRY_EXHAUSTED,
                    }
                    else MonitoringLevel.INFO
                ),
            )
            terminal += 1

        return PollingTickSummary(
            eligible_routes=eligible_routes,
            claimed=len(claimed),
            provider_calls=provider_calls,
            successes=successes,
            deferred=deferred,
            failures=failures,
            terminal=terminal,
        )

    def _seed_eligible_work(
        self,
        *,
        selection: PollingMarketSelection,
        routing: RoutingConfiguration,
        preferences: tuple[tuple[str, UserRoutingPreferences], ...],
        strategies: tuple[PollingStrategy, ...],
        resolver: PollingStrategyResolver,
        now: datetime,
    ) -> int:
        market_sources = {
            (strategy.source.provider_id, strategy.source.source_id): strategy.source
            for strategy in strategies
            if strategy.target is PollingTarget.MARKET
            and strategy.source.provider_id in self._collectors
        }
        eligible = 0
        for owner, user_preferences in preferences:
            for engine in V1_ENGINES:
                modes = effective_engine_modes(routing, user_preferences, engine)
                for mode, active in (
                    ("simulation", modes.simulation),
                    ("execution", modes.execution),
                ):
                    if not active:
                        continue
                    for source in market_sources.values():
                        candidate = PollingWorkItem.create_market(
                            owner=owner,
                            source=source,
                            engine=engine,
                            mode=mode,
                            selection=selection,
                            next_due_at=now,
                        )
                        try:
                            strategy = resolver.resolve(candidate.request)
                        except PollingStrategyResolutionError:
                            continue
                        if not strategy.enabled:
                            continue
                        persisted = self._work_store.ensure(candidate)
                        if (
                            persisted.state is PollingWorkState.DISABLED
                            and persisted.last_reason == "polling_route_disabled"
                        ):
                            persisted = persisted.model_copy(
                                update={
                                    "state": PollingWorkState.PENDING,
                                    "claimed_at": None,
                                    "next_due_at": now,
                                    "last_outcome": "scheduled",
                                    "last_reason": "polling_route_reenabled",
                                }
                            )
                            self._work_store.save(persisted)
                        eligible += 1
        return eligible

    @staticmethod
    def _route_is_active(
        work: PollingWorkItem,
        routing: RoutingConfiguration,
        preferences: Mapping[str, UserRoutingPreferences],
    ) -> bool:
        user_preferences = preferences.get(work.owner)
        if user_preferences is None or work.request.engine not in V1_ENGINES:
            return False
        engine = cast(V1Engine, work.request.engine)
        modes = effective_engine_modes(routing, user_preferences, engine)
        if work.request.mode == "simulation":
            return modes.simulation
        if work.request.mode == "execution":
            return modes.execution
        return False

    def _fetch(
        self,
        work: PollingWorkItem,
        *,
        resolver: PollingStrategyResolver,
        now: datetime,
    ) -> bool:
        collector = self._collectors.get(work.request.source.provider_id)
        if collector is None:
            self._retry_failure(
                work,
                now=now,
                outcome="provider_error",
                reason="polling_provider_not_configured",
                level=MonitoringLevel.ERROR,
            )
            return False

        request = DataCollectionRequest(
            correlation_id=work.request.correlation_id,
            target=DataTarget(work.request.engine),
            source=work.request.source,
            sport=work.selection.sport,
            event_id=work.selection.event_id,
            market=work.selection.market,
        )
        self._record(
            work,
            now=now,
            event_type="provider_query",
            status="fetching",
            reason=None,
            level=MonitoringLevel.INFO,
        )
        started = perf_counter()
        try:
            snapshot = collector.collect(request)
        except TheOddsApiRateLimitError:
            self._retry_failure(
                work,
                now=now,
                outcome="delayed",
                reason="polling_provider_rate_limited",
                level=MonitoringLevel.WARNING,
                duration_ms=_elapsed_ms(started),
            )
            return False
        except (TheOddsApiAuthenticationError, TheOddsApiConfigurationError):
            self._retry_failure(
                work,
                now=now,
                outcome="provider_error",
                reason="polling_provider_configuration_error",
                level=MonitoringLevel.ERROR,
                duration_ms=_elapsed_ms(started),
            )
            return False
        except TheOddsApiPayloadError:
            self._retry_failure(
                work,
                now=now,
                outcome="provider_error",
                reason="polling_provider_payload_invalid",
                level=MonitoringLevel.ERROR,
                duration_ms=_elapsed_ms(started),
            )
            return False
        except (TheOddsApiTransportError, TheOddsApiError):
            self._retry_failure(
                work,
                now=now,
                outcome="unavailable",
                reason="polling_provider_unavailable",
                level=MonitoringLevel.ERROR,
                duration_ms=_elapsed_ms(started),
            )
            return False

        strategy = resolver.resolve(work.request)
        freshness_deadline = snapshot.fetched_at + strategy.freshness_window
        refreshed_request = work.request.model_copy(
            update={
                "fetched_at": snapshot.fetched_at,
                "next_poll_at": freshness_deadline,
                "attempt": 0,
            }
        )
        succeeded = work.model_copy(
            update={
                "request": refreshed_request,
                "state": PollingWorkState.PENDING,
                "claimed_at": None,
                "next_due_at": freshness_deadline,
                "last_outcome": "success",
                "last_reason": "polling_fetch_succeeded",
                "last_successful_fetch_at": snapshot.fetched_at,
                "snapshot": snapshot,
            }
        )
        self._work_store.save(succeeded)
        self._record(
            succeeded,
            now=now,
            event_type="provider_query",
            status="success",
            reason=None,
            level=MonitoringLevel.INFO,
            duration_ms=_elapsed_ms(started),
        )
        return True

    def _retry_failure(
        self,
        work: PollingWorkItem,
        *,
        now: datetime,
        outcome: str,
        reason: str,
        level: MonitoringLevel,
        duration_ms: int | None = None,
    ) -> None:
        request = work.request.model_copy(
            update={"attempt": work.request.attempt + 1}
        )
        failed = work.model_copy(
            update={
                "request": request,
                "state": PollingWorkState.PENDING,
                "claimed_at": None,
                "next_due_at": now,
                "last_outcome": outcome,
                "last_reason": reason,
            }
        )
        self._work_store.save(failed)
        self._record(
            failed,
            now=now,
            event_type="provider_query",
            status=outcome,
            reason=reason,
            level=level,
            duration_ms=duration_ms,
        )

    def _disable(self, work: PollingWorkItem, *, now: datetime, reason: str) -> None:
        disabled = work.model_copy(
            update={
                "state": PollingWorkState.DISABLED,
                "claimed_at": None,
                "next_due_at": now,
                "last_outcome": "disabled",
                "last_reason": reason,
            }
        )
        self._work_store.save(disabled)
        self._record(
            disabled,
            now=now,
            event_type="polling_schedule",
            status="disabled",
            reason=reason,
            level=MonitoringLevel.INFO,
        )

    def _record(
        self,
        work: PollingWorkItem,
        *,
        now: datetime,
        event_type: str,
        status: str,
        reason: str | None,
        level: MonitoringLevel,
        duration_ms: int | None = None,
    ) -> None:
        if self._monitoring_writer is None:
            return
        try:
            self._monitoring_writer.append(
                MonitoringRecord(
                    correlation_id=work.request.correlation_id,
                    occurred_at=now.astimezone(UTC),
                    engine=work.request.engine,
                    mode=work.request.mode,
                    stage="data_aggregation",
                    event_type=event_type,
                    status=status,
                    reason_code=reason,
                    level=level,
                    duration_ms=duration_ms,
                    references={
                        "work_id": str(work.id),
                        "provider_id": work.request.source.provider_id,
                        "source_id": work.request.source.source_id,
                        "target": work.request.target.value,
                    },
                )
            )
        except OSError:
            return


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))
