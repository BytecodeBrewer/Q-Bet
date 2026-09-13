from datetime import UTC, datetime, timedelta
from uuid import UUID

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
