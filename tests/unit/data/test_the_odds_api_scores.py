from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest

from qbet.data import (
    DataSourceMetadata,
    ResultCollectionRequest,
    ResultCollectionStatus,
    ResultProviderTarget,
    SourceTransport,
    THE_ODDS_API_PROVIDER_ID,
    TheOddsApiScoreCollector,
)

CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
SOURCE = DataSourceMetadata(
    provider_id=THE_ODDS_API_PROVIDER_ID,
    source_id="the-odds-api-scores",
    transport=SourceTransport.API,
)


def request() -> ResultCollectionRequest:
    return ResultCollectionRequest(
        match_id="event-123:h2h",
        execution_id="87654321-4321-8765-4321-876543218765",
        correlation_id=CORRELATION_ID,
        source=SOURCE,
        fresh_after=datetime(2026, 9, 19, 18, tzinfo=UTC),
        provider_target=ResultProviderTarget(sport="soccer_epl", event_id="event-123"),
    )


def payload(*, completed: bool = True, scores=True, event_id: str = "event-123") -> bytes:
    score_value = (
        [
            {"name": "Home", "score": "2"},
            {"name": "Away", "score": "1"},
        ]
        if scores is True
        else scores
    )
    return json.dumps(
        [
            {
                "id": event_id,
                "sport_key": "soccer_epl",
                "completed": completed,
                "last_update": "2026-09-19T19:00:00Z",
                "scores": score_value,
            }
        ]
    ).encode()


def test_completed_event_uses_exact_event_filter_and_normalizes_score_evidence() -> None:
    urls: list[str] = []
    collector = TheOddsApiScoreCollector(
        api_key="test-secret",
        days_from=2,
        http_get=lambda url: (urls.append(url) or 200, {}, payload()),
    )

    outcome = collector.collect(request())

    assert outcome.status is ResultCollectionStatus.AVAILABLE
    assert outcome.result is not None
    assert outcome.result.completed is True
    assert outcome.result.provider_outcome is None
    assert [(score.participant, str(score.score)) for score in outcome.result.scores] == [
        ("Home", "2"),
        ("Away", "1"),
    ]
    assert len(urls) == 1
    parsed = urlparse(urls[0])
    assert parsed.path == "/v4/sports/soccer_epl/scores/"
    query = parse_qs(parsed.query)
    assert query["eventIds"] == ["event-123"]
    assert query["daysFrom"] == ["2"]
    assert query["dateFormat"] == ["iso"]


def test_live_scores_are_partial_and_do_not_expose_result() -> None:
    collector = TheOddsApiScoreCollector(
        api_key="test-secret",
        http_get=lambda _: (200, {}, payload(completed=False)),
    )

    outcome = collector.collect(request())

    assert outcome.status is ResultCollectionStatus.PARTIAL
    assert outcome.result is None
    assert outcome.reason_code == "result_in_progress"


def test_live_event_without_scores_is_not_yet_available() -> None:
    collector = TheOddsApiScoreCollector(
        api_key="test-secret",
        http_get=lambda _: (200, {}, payload(completed=False, scores=None)),
    )

    outcome = collector.collect(request())

    assert outcome.status is ResultCollectionStatus.NOT_YET_AVAILABLE
    assert outcome.reason_code == "result_not_yet_available"


def test_empty_response_remains_retryable_instead_of_inventing_cancellation() -> None:
    collector = TheOddsApiScoreCollector(
        api_key="test-secret",
        http_get=lambda _: (200, {}, b"[]"),
    )

    outcome = collector.collect(request())

    assert outcome.status is ResultCollectionStatus.NOT_YET_AVAILABLE
    assert outcome.reason_code == "result_event_not_found"


def test_wrong_event_identity_fails_closed() -> None:
    collector = TheOddsApiScoreCollector(
        api_key="test-secret",
        http_get=lambda _: (200, {}, payload(event_id="other-event")),
    )

    outcome = collector.collect(request())

    assert outcome.status is ResultCollectionStatus.MISMATCHED
    assert outcome.reason_code == "result_event_identity_mismatch"


def test_malformed_completed_scores_are_invalid() -> None:
    collector = TheOddsApiScoreCollector(
        api_key="test-secret",
        http_get=lambda _: (200, {}, payload(scores=[{"name": "Home", "score": "bad"}])),
    )

    outcome = collector.collect(request())

    assert outcome.status is ResultCollectionStatus.INVALID
    assert outcome.reason_code == "result_provider_payload_invalid"


@pytest.mark.parametrize(
    ("status", "expected_status", "reason"),
    [
        (401, ResultCollectionStatus.FAILED, "result_provider_auth_failed"),
        (429, ResultCollectionStatus.UNKNOWN, "result_provider_rate_limited"),
        (503, ResultCollectionStatus.UNKNOWN, "result_provider_unavailable"),
    ],
)
def test_provider_failures_map_to_safe_stable_outcomes(
    status: int,
    expected_status: ResultCollectionStatus,
    reason: str,
) -> None:
    collector = TheOddsApiScoreCollector(
        api_key="super-secret-value",
        http_get=lambda _: (status, {}, b"provider raw secret-ish body"),
    )

    outcome = collector.collect(request())

    assert outcome.status is expected_status
    assert outcome.reason_code == reason
    assert "super-secret-value" not in str(outcome)
    assert "provider raw secret-ish body" not in str(outcome)


def test_missing_provider_target_is_safe_configuration_failure_without_http() -> None:
    calls: list[str] = []
    collector = TheOddsApiScoreCollector(
        api_key="test-secret",
        http_get=lambda url: (calls.append(url) or 200, {}, b"[]"),
    )
    no_target = request().model_copy(update={"provider_target": None})

    outcome = collector.collect(no_target)

    assert outcome.status is ResultCollectionStatus.FAILED
    assert outcome.reason_code == "result_provider_configuration_invalid"
    assert calls == []


def test_days_from_is_bounded_by_provider_contract() -> None:
    with pytest.raises(ValueError, match="between 1 and 3"):
        TheOddsApiScoreCollector(api_key="test-secret", days_from=4)