from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from qbet.data import (
    DataSourceMetadata,
    DeterministicResultCollector,
    DeterministicResultFixture,
    NormalizedMatchResult,
    ResultAvailability,
    ResultCollectionRequest,
    ResultCollectionStatus,
    ResultCollector,
    SourceTransport,
)

SOURCE = DataSourceMetadata(
    provider_id="result_fixture", source_id="settlement-feed", transport=SourceTransport.IN_MEMORY
)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
OBSERVED_AT = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def request(**changes: object) -> ResultCollectionRequest:
    values: dict[str, object] = {
        "match_id": "match-1",
        "execution_id": "execution-1",
        "correlation_id": CORRELATION_ID,
        "source": SOURCE,
        "fresh_after": datetime(2026, 9, 11, 11, 0, tzinfo=UTC),
    }
    values.update(changes)
    return ResultCollectionRequest.model_validate(values)


def result(**changes: object) -> NormalizedMatchResult:
    values: dict[str, object] = {
        "match_id": "match-1",
        "execution_id": "execution-1",
        "correlation_id": CORRELATION_ID,
        "source": SOURCE,
        "availability": ResultAvailability.AVAILABLE,
        "observed_at": OBSERVED_AT,
        "provider_outcome": "home_win",
    }
    values.update(changes)
    return NormalizedMatchResult.model_validate(values)


def collector(fixture: DeterministicResultFixture) -> DeterministicResultCollector:
    value = DeterministicResultCollector((fixture,))
    assert isinstance(value, ResultCollector)
    return value


def test_available_result_preserves_identity_source_and_correlation() -> None:
    outcome = collector(DeterministicResultFixture(result=result())).collect(request())

    normalized = outcome.require_result_for_settlement_or_reporting()
    assert outcome.status is ResultCollectionStatus.AVAILABLE
    assert normalized.correlation_id == CORRELATION_ID
    assert normalized.source == SOURCE
    assert normalized.execution_id == "execution-1"
    assert normalized.provider_outcome == "home_win"


@pytest.mark.parametrize(
    ("status", "reason_code"),
    [
        (ResultCollectionStatus.FAILED, "provider_failed"),
        (ResultCollectionStatus.CANCELLED, "event_cancelled"),
        (ResultCollectionStatus.PARTIAL, "result_partial"),
        (ResultCollectionStatus.UNKNOWN, "result_unknown"),
        (ResultCollectionStatus.INVALID, "result_invalid"),
    ],
)
def test_non_available_fixture_states_are_typed_before_settlement(
    status: ResultCollectionStatus, reason_code: str
) -> None:
    outcome = collector(
        DeterministicResultFixture(result=result(), status=status, reason_code=reason_code)
    ).collect(request())

    assert outcome.status is status
    assert outcome.reason_code == reason_code
    with pytest.raises(ValueError, match="not available"):
        outcome.require_result_for_settlement_or_reporting()


def test_missing_fixture_returns_not_yet_available() -> None:
    outcome = DeterministicResultCollector().collect(request())

    assert outcome.status is ResultCollectionStatus.NOT_YET_AVAILABLE
    assert outcome.reason_code == "result_not_yet_available"


@pytest.mark.parametrize(
    "changes",
    [
        {"execution_id": "other-execution"},
        {"correlation_id": UUID("87654321-4321-8765-4321-876543218765")},
        {
            "source": DataSourceMetadata(
                provider_id="other", source_id="other", transport=SourceTransport.API
            )
        },
    ],
)
def test_identity_correlation_and_source_mismatches_are_rejected(
    changes: dict[str, object],
) -> None:
    outcome = collector(DeterministicResultFixture(result=result(**changes))).collect(request())

    assert outcome.status is ResultCollectionStatus.MISMATCHED
    assert outcome.reason_code == "result_identity_mismatch"


def test_stale_results_are_rejected_before_the_post_event_boundary() -> None:
    outcome = collector(
        DeterministicResultFixture(
            result=result(observed_at=datetime(2026, 9, 11, 10, 0, tzinfo=UTC))
        )
    ).collect(request())

    assert outcome.status is ResultCollectionStatus.STALE
    assert outcome.reason_code == "result_stale"


def test_fixture_replay_is_deterministic() -> None:
    source = collector(DeterministicResultFixture(result=result()))

    assert source.collect(request()) == source.collect(request())


def test_result_models_reject_invalid_contracts() -> None:
    with pytest.raises(ValueError, match="require an outcome"):
        result(provider_outcome=None)
    with pytest.raises(ValueError, match="require a result"):
        DeterministicResultFixture()
