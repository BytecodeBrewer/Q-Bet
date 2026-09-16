from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from qbet.data import (
    DataSourceMetadata,
    PollingOutcome,
    PollingRequest,
    PollingSchedule,
    PollingTarget,
    SmartPollingConfiguration,
    SmartPollingPolicy,
    SourceTransport,
)
from qbet.data.polling import (
    PollingCapacityClass,
    PollingStrategy,
    PollingStrategyResolutionError,
    PollingStrategyResolver,
)

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
SOURCE = DataSourceMetadata(
    provider_id="fixture", source_id="polling", transport=SourceTransport.IN_MEMORY
)


def request(target: PollingTarget, **changes: object) -> PollingRequest:
    values: dict[str, object] = {
        "source": SOURCE,
        "target": target,
        "match_id": "match-1",
        "correlation_id": UUID("12345678-1234-5678-1234-567812345678"),
        "fetched_at": NOW - timedelta(minutes=10),
        "event_starts_at": NOW + timedelta(hours=1),
        "next_poll_at": NOW,
        "attempt": 0,
        "mode": "simulation",
        "engine": "sports_capital",
    }
    values.update(changes)
    return PollingRequest.model_validate(values)


def strategy(target: PollingTarget, **changes: object) -> PollingStrategy:
    values: dict[str, object] = {
        "source": SOURCE,
        "target": target,
        "freshness_window": timedelta(minutes=5),
        "market_interval": timedelta(minutes=5) if target is PollingTarget.MARKET else None,
        "result_retry_interval": timedelta(minutes=10),
        "max_attempts": 3,
        "capacity_class": PollingCapacityClass.FREE,
        "request_cost_units": 1,
    }
    values.update(changes)
    return PollingStrategy.model_validate(values)


def test_market_refresh_is_bounded_and_deterministic() -> None:
    policy = SmartPollingPolicy()
    value = request(PollingTarget.MARKET)

    assert policy.decide(value) == policy.decide(value)
    assert policy.decide(value).outcome is PollingOutcome.SCHEDULED


def test_fetched_at_deterministically_controls_market_freshness() -> None:
    policy = SmartPollingPolicy(
        SmartPollingConfiguration(market_freshness_window=timedelta(minutes=5))
    )
    fresh = policy.decide(request(PollingTarget.MARKET, fetched_at=NOW - timedelta(minutes=4)))
    stale = policy.decide(request(PollingTarget.MARKET, fetched_at=NOW - timedelta(minutes=5)))
    schedule = PollingSchedule()

    assert fresh.outcome is PollingOutcome.SKIPPED_FRESH
    assert fresh.freshness_deadline == NOW + timedelta(minutes=1)
    assert stale.outcome is PollingOutcome.SCHEDULED
    assert schedule.schedule(fresh) is fresh
    assert schedule.entries() == ()


def test_market_event_boundary_is_expired() -> None:
    decision = SmartPollingPolicy().decide(
        request(
            PollingTarget.MARKET,
            event_starts_at=NOW + timedelta(minutes=1),
        )
    )

    assert decision.outcome is PollingOutcome.EXPIRED


def test_result_retry_terminal_and_attempt_limits_are_typed() -> None:
    policy = SmartPollingPolicy(SmartPollingConfiguration(max_attempts=2))

    assert (
        policy.decide(request(PollingTarget.RESULT, next_poll_at=NOW + timedelta(hours=2))).outcome
        is PollingOutcome.SCHEDULED
    )
    assert (
        policy.decide(request(PollingTarget.RESULT, result_available=True)).outcome
        is PollingOutcome.TERMINAL
    )
    assert (
        policy.decide(request(PollingTarget.RESULT, attempt=2)).outcome
        is PollingOutcome.RETRY_EXHAUSTED
    )
    assert (
        policy.decide(
            request(
                PollingTarget.RESULT,
                next_poll_at=NOW + timedelta(hours=2),
                result_partial=True,
            )
        ).reason
        == "partial_result_retry"
    )
    assert policy.decide(request(PollingTarget.RESULT)).outcome is PollingOutcome.INVALID


def test_schedule_coalesces_only_the_same_target_identity_and_correlation() -> None:
    policy = SmartPollingPolicy()
    schedule = PollingSchedule()
    first = policy.decide(request(PollingTarget.MARKET))
    duplicate = policy.decide(request(PollingTarget.MARKET))
    result = policy.decide(request(PollingTarget.RESULT, next_poll_at=NOW + timedelta(hours=2)))

    assert schedule.schedule(first).outcome is PollingOutcome.SCHEDULED
    assert schedule.schedule(duplicate).outcome is PollingOutcome.DUPLICATE
    assert schedule.schedule(result).outcome is PollingOutcome.SCHEDULED
    assert len(schedule.entries()) == 2


def test_schedule_preserves_mode_engine_and_source_isolation() -> None:
    policy = SmartPollingPolicy()
    schedule = PollingSchedule()
    simulation = policy.decide(request(PollingTarget.MARKET))
    execution = policy.decide(request(PollingTarget.MARKET, mode="execution"))
    other_engine = policy.decide(request(PollingTarget.MARKET, engine="bonus"))

    assert schedule.schedule(simulation).outcome is PollingOutcome.SCHEDULED
    assert schedule.schedule(execution).outcome is PollingOutcome.SCHEDULED
    assert schedule.schedule(other_engine).outcome is PollingOutcome.SCHEDULED
    assert len(schedule.entries()) == 3


def test_schedule_allows_rescheduling_after_completion() -> None:
    policy = SmartPollingPolicy()
    schedule = PollingSchedule()
    first = policy.decide(request(PollingTarget.MARKET))
    later = policy.decide(request(PollingTarget.MARKET, next_poll_at=NOW + timedelta(minutes=15)))

    assert schedule.schedule(first).outcome is PollingOutcome.SCHEDULED
    schedule.complete(first.request)
    assert schedule.schedule(later).outcome is PollingOutcome.SCHEDULED
    assert schedule.entries() == (later,)


def test_engine_specific_strategy_overrides_provider_target_default() -> None:
    default = strategy(
        PollingTarget.MARKET,
        market_interval=None,
        market_refresh_points=(timedelta(hours=24), timedelta(hours=12), timedelta(hours=1)),
    )
    override = strategy(
        PollingTarget.MARKET,
        engine="bonus",
        market_interval=None,
        market_refresh_points=(timedelta(minutes=30),),
        capacity_class=PollingCapacityClass.PAID,
    )
    resolver = PollingStrategyResolver((default, override))

    assert resolver.resolve(request(PollingTarget.MARKET, engine="bonus")) == override
    assert resolver.resolve(request(PollingTarget.MARKET, engine="sports_capital")) == default


def test_strategy_resolution_rejects_equal_priority_ambiguity() -> None:
    first = strategy(PollingTarget.MARKET)
    duplicate = first.model_copy()
    resolver = PollingStrategyResolver((first, duplicate))

    with pytest.raises(PollingStrategyResolutionError, match="ambiguous_polling_strategy"):
        resolver.resolve(request(PollingTarget.MARKET))


def test_persistable_market_refresh_points_are_used_far_to_near() -> None:
    event_starts_at = NOW + timedelta(hours=30)
    configured = strategy(
        PollingTarget.MARKET,
        market_interval=None,
        market_refresh_points=(timedelta(hours=24), timedelta(hours=12), timedelta(hours=1)),
    )
    policy = SmartPollingPolicy(resolver=PollingStrategyResolver((configured,)))

    first = policy.decide(
        request(
            PollingTarget.MARKET,
            event_starts_at=event_starts_at,
            next_poll_at=NOW,
            fetched_at=NOW - timedelta(hours=1),
        )
    )
    second = policy.decide(
        request(
            PollingTarget.MARKET,
            event_starts_at=event_starts_at,
            next_poll_at=event_starts_at - timedelta(hours=23),
            fetched_at=NOW - timedelta(hours=1),
        )
    )
    after_last = policy.decide(
        request(
            PollingTarget.MARKET,
            event_starts_at=event_starts_at,
            next_poll_at=event_starts_at - timedelta(minutes=30),
            fetched_at=NOW - timedelta(hours=1),
        )
    )

    assert first.scheduled_for == event_starts_at - timedelta(hours=24)
    assert second.scheduled_for == event_starts_at - timedelta(hours=12)
    assert after_last.outcome is PollingOutcome.EXPIRED


def test_capacity_and_enabled_configuration_produce_typed_non_network_outcomes() -> None:
    exhausted = strategy(PollingTarget.MARKET, capacity_units=0)
    disabled = strategy(PollingTarget.MARKET, enabled=False)

    exhausted_decision = SmartPollingPolicy(
        resolver=PollingStrategyResolver((exhausted,))
    ).decide(request(PollingTarget.MARKET))
    disabled_decision = SmartPollingPolicy(
        resolver=PollingStrategyResolver((disabled,))
    ).decide(request(PollingTarget.MARKET))

    assert exhausted_decision.outcome is PollingOutcome.DEFERRED_CAPACITY
    assert exhausted_decision.reason == "polling_capacity_insufficient"
    assert disabled_decision.outcome is PollingOutcome.DISABLED



def test_persisted_result_strategy_controls_retry_interval_and_capacity() -> None:
    configured = strategy(
        PollingTarget.RESULT,
        result_retry_interval=timedelta(minutes=20),
        capacity_units=1,
        request_cost_units=2,
    )
    policy = SmartPollingPolicy(resolver=PollingStrategyResolver((configured,)))
    deferred = policy.decide(
        request(PollingTarget.RESULT, next_poll_at=NOW + timedelta(hours=2))
    )
    assert deferred.outcome is PollingOutcome.DEFERRED_CAPACITY

    available = configured.model_copy(update={"capacity_units": 2})
    scheduled = SmartPollingPolicy(
        resolver=PollingStrategyResolver((available,))
    ).decide(request(PollingTarget.RESULT, next_poll_at=NOW + timedelta(hours=2)))
    assert scheduled.scheduled_for == NOW + timedelta(hours=2, minutes=20)

def test_result_polling_stops_when_tracking_is_not_required() -> None:
    policy = SmartPollingPolicy(resolver=PollingStrategyResolver(()))
    decision = policy.decide(
        request(PollingTarget.RESULT, result_tracking_required=False)
    )

    assert decision.outcome is PollingOutcome.TERMINAL
    assert decision.reason == "result_tracking_not_required"




def test_strategy_validation_rejects_web_transport_for_polling() -> None:
    web_source = DataSourceMetadata(
        provider_id="fixture", source_id="web", transport=SourceTransport.WEB
    )
    with pytest.raises(ValidationError, match="API or in-memory"):
        strategy(PollingTarget.MARKET, source=web_source)


def test_strategy_validation_rejects_unknown_engine_identifier() -> None:
    with pytest.raises(ValidationError):
        strategy(PollingTarget.MARKET, engine="ticket")




def test_strategy_rejects_unknown_persisted_fields() -> None:
    payload = strategy(PollingTarget.MARKET).model_dump(mode="json")
    payload["api_key"] = "must-not-be-accepted"
    with pytest.raises(ValidationError):
        PollingStrategy.model_validate(payload)

def test_strategy_json_round_trip_preserves_persisted_timing_and_capacity() -> None:
    original = strategy(
        PollingTarget.MARKET,
        engine="bonus",
        market_interval=None,
        market_refresh_points=(timedelta(hours=24), timedelta(hours=1)),
        capacity_class=PollingCapacityClass.TEST,
        capacity_units=50,
        request_cost_units=2,
    )

    assert PollingStrategy.model_validate(original.model_dump(mode="json")) == original


def test_strategy_validation_rejects_impossible_attempts_and_nonpositive_points() -> None:
    with pytest.raises(ValidationError):
        strategy(PollingTarget.RESULT, max_attempts=0)
    with pytest.raises(ValidationError, match="positive"):
        strategy(
            PollingTarget.MARKET,
            market_interval=None,
            market_refresh_points=(timedelta(),),
        )


def test_strategy_validation_rejects_ambiguous_point_and_interval_configuration() -> None:
    with pytest.raises(ValidationError, match="points or a fallback interval"):
        strategy(
            PollingTarget.MARKET,
            market_interval=timedelta(minutes=5),
            market_refresh_points=(timedelta(hours=1),),
        )

def test_strategy_validation_rejects_duplicate_or_unordered_refresh_points() -> None:
    with pytest.raises(ValidationError, match="unique"):
        strategy(
            PollingTarget.MARKET,
            market_interval=None,
            market_refresh_points=(timedelta(hours=1), timedelta(hours=1)),
        )
    with pytest.raises(ValidationError, match="far-to-near"):
        strategy(
            PollingTarget.MARKET,
            market_interval=None,
            market_refresh_points=(timedelta(hours=1), timedelta(hours=12)),
        )
