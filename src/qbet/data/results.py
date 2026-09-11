"""Provider-neutral, read-only result collection contracts and fixtures."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import AwareDatetime, model_validator

from qbet.data.models import DataSourceMetadata
from qbet.domain.models import DomainModel, Identifier


class ResultAvailability(StrEnum):
    AVAILABLE = "available"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    NOT_YET_AVAILABLE = "not_yet_available"


class ResultCollectionStatus(StrEnum):
    AVAILABLE = "available"
    STALE = "stale"
    INVALID = "invalid"
    MISMATCHED = "mismatched"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    NOT_YET_AVAILABLE = "not_yet_available"


class ResultCollectionRequest(DomainModel):
    """Provider-neutral selection and correlation context for one result read."""

    match_id: Identifier
    execution_id: Identifier
    correlation_id: UUID
    source: DataSourceMetadata
    fresh_after: AwareDatetime


class NormalizedMatchResult(DomainModel):
    """Validated provider outcome with no settlement or capital side effects."""

    match_id: Identifier
    execution_id: Identifier
    correlation_id: UUID
    source: DataSourceMetadata
    availability: ResultAvailability
    observed_at: AwareDatetime
    provider_outcome: Identifier | None = None
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_availability_fields(self) -> "NormalizedMatchResult":
        if self.availability is ResultAvailability.AVAILABLE:
            if self.provider_outcome is None or self.reason_code is not None:
                raise ValueError("available results require an outcome and no reason_code")
        elif self.reason_code is None:
            raise ValueError("unavailable results require a reason_code")
        return self


class ResultCollectionOutcome(DomainModel):
    """Stable application result returned before settlement or reporting consumes data."""

    request: ResultCollectionRequest
    status: ResultCollectionStatus
    result: NormalizedMatchResult | None = None
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_result_boundary(self) -> "ResultCollectionOutcome":
        if self.status is ResultCollectionStatus.AVAILABLE:
            if self.result is None or self.reason_code is not None:
                raise ValueError("available collection outcomes require a result only")
        elif self.result is not None:
            raise ValueError("non-available collection outcomes must not expose a result")
        elif self.reason_code is None:
            raise ValueError("non-available collection outcomes require a reason_code")
        return self

    def require_result_for_settlement_or_reporting(self) -> NormalizedMatchResult:
        """Prevent unvalidated collection states from crossing the post-event boundary."""

        if self.status is not ResultCollectionStatus.AVAILABLE or self.result is None:
            raise ValueError("result collection outcome is not available")
        return self.result


@runtime_checkable
class ResultCollector(Protocol):
    """Separate read-only result boundary; it never invokes settlement or reporting."""

    def collect(self, request: ResultCollectionRequest) -> ResultCollectionOutcome: ...


class DeterministicResultFixture(DomainModel):
    """Fixture-backed provider response for deterministic result collection tests."""

    result: NormalizedMatchResult | None = None
    status: ResultCollectionStatus = ResultCollectionStatus.AVAILABLE
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_fixture_contract(self) -> "DeterministicResultFixture":
        if self.status is ResultCollectionStatus.AVAILABLE and self.result is None:
            raise ValueError("available fixtures require a result")
        if self.status is not ResultCollectionStatus.AVAILABLE and self.reason_code is None:
            raise ValueError("non-available fixtures require a reason_code")
        return self


class DeterministicResultCollector:
    """Pure fixture collector used until a permitted official result adapter is connected."""

    def __init__(self, fixtures: tuple[DeterministicResultFixture, ...] = ()) -> None:
        indexed: dict[str, DeterministicResultFixture] = {}
        for fixture in fixtures:
            if fixture.result is None:
                continue
            if fixture.result.match_id in indexed:
                raise ValueError("result fixtures must use distinct match_id values")
            indexed[fixture.result.match_id] = fixture
        self._fixtures = indexed

    def collect(self, request: ResultCollectionRequest) -> ResultCollectionOutcome:
        fixture = self._fixtures.get(request.match_id)
        if fixture is None:
            return _outcome(
                request,
                ResultCollectionStatus.NOT_YET_AVAILABLE,
                "result_not_yet_available",
            )
        result = fixture.result
        assert result is not None
        if fixture.status is not ResultCollectionStatus.AVAILABLE:
            return _outcome(request, fixture.status, fixture.reason_code)
        if not _matches_request(result, request):
            return _outcome(request, ResultCollectionStatus.MISMATCHED, "result_identity_mismatch")
        if result.observed_at < request.fresh_after:
            return _outcome(request, ResultCollectionStatus.STALE, "result_stale")
        return ResultCollectionOutcome(
            request=request, status=ResultCollectionStatus.AVAILABLE, result=result
        )


def _matches_request(result: NormalizedMatchResult, request: ResultCollectionRequest) -> bool:
    return (
        result.match_id == request.match_id
        and result.execution_id == request.execution_id
        and result.correlation_id == request.correlation_id
        and result.source == request.source
    )


def _outcome(
    request: ResultCollectionRequest,
    status: ResultCollectionStatus,
    reason_code: Identifier | None,
) -> ResultCollectionOutcome:
    assert status is not ResultCollectionStatus.AVAILABLE
    assert reason_code is not None
    return ResultCollectionOutcome(request=request, status=status, reason_code=reason_code)
