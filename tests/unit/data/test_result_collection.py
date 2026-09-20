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
    ResultCollectionOutcome,
    ResultCollectionStatus,
    ResultCollector,
    SourceTransport,
)
from qbet.settlement import SettlementService

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
    ("availability", "status", "reason_code"),
    [
        (ResultAvailability.FAILED, ResultCollectionStatus.FAILED, "provider_failed"),
        (ResultAvailability.CANCELLED, ResultCollectionStatus.CANCELLED, "event_cancelled"),
        (ResultAvailability.PARTIAL, ResultCollectionStatus.PARTIAL, "result_partial"),
        (ResultAvailability.UNKNOWN, ResultCollectionStatus.UNKNOWN, "result_unknown"),
        (
            ResultAvailability.NOT_YET_AVAILABLE,
            ResultCollectionStatus.NOT_YET_AVAILABLE,
            "result_not_ready",
        ),
    ],
)
def test_non_available_fixture_states_are_typed_before_settlement(
    availability: ResultAvailability, status: ResultCollectionStatus, reason_code: str
) -> None:
    outcome = collector(
        DeterministicResultFixture(
            result=result(availability=availability, reason_code=reason_code),
            status=status,
            reason_code=reason_code,
        )
    ).collect(request())

    assert outcome.status is status
    assert outcome.reason_code == reason_code
    with pytest.raises(ValueError, match="not available"):
        SettlementService().collected_result_for_settlement(outcome)


def test_missing_fixture_returns_not_yet_available() -> None:
    outcome = DeterministicResultCollector().collect(request())

    assert outcome.status is ResultCollectionStatus.NOT_YET_AVAILABLE
    assert outcome.reason_code == "result_not_yet_available"


def test_non_available_resultless_fixture_is_returned_by_match_id() -> None:
    outcome = collector(
        DeterministicResultFixture(
            match_id="match-1",
            status=ResultCollectionStatus.CANCELLED,
            reason_code="event_cancelled",
        )
    ).collect(request())

    assert outcome.status is ResultCollectionStatus.CANCELLED
    assert outcome.reason_code == "event_cancelled"


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


def test_available_outcome_rejects_result_identity_mismatch() -> None:
    with pytest.raises(ValueError, match="available result only"):
        ResultCollectionOutcome(
            request=request(),
            status=ResultCollectionStatus.AVAILABLE,
            result=result(execution_id="other-execution"),
        )


def test_fixture_replay_is_deterministic() -> None:
    source = collector(DeterministicResultFixture(result=result()))

    assert source.collect(request()) == source.collect(request())


def test_result_models_reject_invalid_contracts() -> None:
    with pytest.raises(ValueError, match="require completed final score evidence"):
        result(provider_outcome=None)
    with pytest.raises(ValueError, match="require match_id"):
        DeterministicResultFixture()


@pytest.mark.parametrize(
    "availability",
    [
        ResultAvailability.FAILED,
        ResultAvailability.CANCELLED,
        ResultAvailability.PARTIAL,
        ResultAvailability.UNKNOWN,
        ResultAvailability.NOT_YET_AVAILABLE,
    ],
)
def test_unavailable_results_cannot_be_declared_available(availability: ResultAvailability) -> None:
    with pytest.raises(ValueError, match="available result"):
        DeterministicResultFixture(
            result=result(availability=availability, reason_code="provider_not_available")
        )
