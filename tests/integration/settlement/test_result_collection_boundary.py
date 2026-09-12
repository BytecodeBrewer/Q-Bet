from datetime import UTC, datetime

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
    SourceTransport,
)
from qbet.execution.models import ExecutionRecord, Lifecycle
from qbet.execution.service import ExecutionService
from tests.unit.execution.test_service import ledger, proposal


def collected_result(
    record: ExecutionRecord,
    *,
    availability: ResultAvailability = ResultAvailability.AVAILABLE,
    match_id: str | None = None,
) -> ResultCollectionOutcome:
    source = DataSourceMetadata(
        provider_id="result_fixture",
        source_id="settlement-feed",
        transport=SourceTransport.IN_MEMORY,
    )
    request = ResultCollectionRequest(
        match_id=match_id or record.proposal.work.opportunity_id,
        execution_id=str(record.proposal.work.id),
        correlation_id=record.proposal.work.correlation_id,
        source=source,
        fresh_after=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = NormalizedMatchResult(
        match_id=request.match_id,
        execution_id=request.execution_id,
        correlation_id=request.correlation_id,
        source=source,
        availability=availability,
        observed_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        provider_outcome="settled" if availability is ResultAvailability.AVAILABLE else None,
        reason_code=None if availability is ResultAvailability.AVAILABLE else "not_available",
    )
    fixture = DeterministicResultFixture(
        result=result,
        status=ResultCollectionStatus.AVAILABLE
        if availability is ResultAvailability.AVAILABLE
        else ResultCollectionStatus.CANCELLED,
        reason_code=None if availability is ResultAvailability.AVAILABLE else "not_available",
    )
    return DeterministicResultCollector((fixture,)).collect(request)


def test_collected_result_reaches_existing_execution_settlement_path() -> None:
    record = ExecutionRecord(proposal=proposal())
    outcome = collected_result(record)

    settled, _ = ExecutionService().decide(
        record,
        ledger(),
        actor="owner",
        owner="owner",
        approve=True,
        now=datetime(2026, 1, 1, tzinfo=UTC),
        collected_result=outcome,
    )

    assert settled.state is Lifecycle.SETTLED


def test_unavailable_collected_result_stops_before_settlement() -> None:
    record = ExecutionRecord(proposal=proposal())
    outcome = collected_result(record, availability=ResultAvailability.CANCELLED)

    with pytest.raises(ValueError, match="not available"):
        ExecutionService().decide(
            record,
            ledger(),
            actor="owner",
            owner="owner",
            approve=True,
            now=datetime(2026, 1, 1, tzinfo=UTC),
            collected_result=outcome,
        )


def test_wrong_match_result_stops_before_settlement_or_ledger_change() -> None:
    record = ExecutionRecord(proposal=proposal())
    original = ledger()
    outcome = collected_result(record, match_id="other-match")

    with pytest.raises(ValueError, match="collected_result_identity_mismatch"):
        ExecutionService().decide(
            record,
            original,
            actor="owner",
            owner="owner",
            approve=True,
            now=datetime(2026, 1, 1, tzinfo=UTC),
            collected_result=outcome,
        )

    assert original.balance.available == ledger().balance.available
    assert original.balance.reserved == 0
    assert original.balance.locked == 0
    assert original.balance.pending == 0
