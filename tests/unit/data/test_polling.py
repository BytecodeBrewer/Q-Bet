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
        "freshness_deadline": NOW - timedelta(minutes=1),
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


def test_fresh_market_data_is_skipped_without_queue_entry() -> None:
    decision = SmartPollingPolicy().decide(
        request(PollingTarget.MARKET, freshness_deadline=NOW + timedelta(minutes=1))
    )
    schedule = PollingSchedule()

    assert decision.outcome is PollingOutcome.SKIPPED_FRESH
    assert schedule.schedule(decision) is decision
    assert schedule.entries() == ()


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
