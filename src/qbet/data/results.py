"""Provider-neutral, read-only result collection contracts and fixtures."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

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


_STATUS_FOR_UNAVAILABLE_RESULT = {
    ResultAvailability.FAILED: ResultCollectionStatus.FAILED,
    ResultAvailability.CANCELLED: ResultCollectionStatus.CANCELLED,
    ResultAvailability.PARTIAL: ResultCollectionStatus.PARTIAL,
    ResultAvailability.UNKNOWN: ResultCollectionStatus.UNKNOWN,
    ResultAvailability.NOT_YET_AVAILABLE: ResultCollectionStatus.NOT_YET_AVAILABLE,
}


class ResultProviderTarget(DomainModel):
    """Provider event identity kept separate from Q-Bet opportunity identity."""

    sport: Identifier
    event_id: Identifier


class ResultScore(DomainModel):
    """Provider-neutral final score evidence for one participant."""

    participant: Identifier
    score: Decimal = Field(ge=Decimal(0), allow_inf_nan=False)


class ResultCollectionRequest(DomainModel):
    """Provider-neutral selection and correlation context for one result read."""

    match_id: Identifier
    execution_id: Identifier
    correlation_id: UUID
    source: DataSourceMetadata
    fresh_after: AwareDatetime
    provider_target: ResultProviderTarget | None = None


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
    provider_target: ResultProviderTarget | None = None
    completed: bool | None = None
    scores: tuple[ResultScore, ...] = ()

    @model_validator(mode="after")
    def validates_availability_fields(self) -> "NormalizedMatchResult":
        if self.availability is ResultAvailability.AVAILABLE:
            if self.reason_code is not None:
                raise ValueError("available results must not include a reason_code")
            if self.provider_outcome is None:
                if self.completed is not True or len(self.scores) < 2:
                    raise ValueError(
                        "score-only available results require completed final score evidence"
                    )
            if self.completed is False:
                raise ValueError("available results cannot describe an incomplete event")
            if len({score.participant for score in self.scores}) != len(self.scores):
                raise ValueError("result scores must use distinct participants")
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
            if (
                self.result is None
                or self.result.availability is not ResultAvailability.AVAILABLE
                or not _matches_request(self.result, self.request)
                or self.reason_code is not None
            ):
                raise ValueError("available collection outcomes require an available result only")
        elif self.result is not None:
            raise ValueError("non-available collection outcomes must not expose a result")
        elif self.reason_code is None:
            raise ValueError("non-available collection outcomes require a reason_code")
        return self

    def require_result_for_settlement_or_reporting(self) -> NormalizedMatchResult:
        """Prevent unvalidated collection states from crossing the post-event boundary."""

        if (
            self.status is not ResultCollectionStatus.AVAILABLE
            or self.result is None
            or self.result.availability is not ResultAvailability.AVAILABLE
        ):
            raise ValueError("result collection outcome is not available")
        return self.result


@runtime_checkable
class ResultCollector(Protocol):
    """Separate read-only result boundary; it never invokes settlement or reporting."""

    def collect(self, request: ResultCollectionRequest) -> ResultCollectionOutcome: ...


class DeterministicResultFixture(DomainModel):
    """Fixture-backed provider response for deterministic result collection tests."""

    match_id: Identifier | None = None
    result: NormalizedMatchResult | None = None
    status: ResultCollectionStatus = ResultCollectionStatus.AVAILABLE
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def validates_fixture_contract(self) -> "DeterministicResultFixture":
        if (
            self.match_id is not None
            and self.result is not None
            and self.match_id != self.result.match_id
        ):
            raise ValueError("fixture match_id must match result.match_id")
        if self.result is None and self.match_id is None:
            raise ValueError("fixtures without results require match_id")
        if self.status is ResultCollectionStatus.AVAILABLE and self.result is None:
            raise ValueError("available fixtures require a result")
        if self.status is ResultCollectionStatus.AVAILABLE and (
            self.result is None or self.result.availability is not ResultAvailability.AVAILABLE
        ):
            raise ValueError("available fixtures require an available result")
        if self.status is not ResultCollectionStatus.AVAILABLE and self.reason_code is None:
            raise ValueError("non-available fixtures require a reason_code")
        if self.result is not None and self.result.availability is not ResultAvailability.AVAILABLE:
            expected_status = _STATUS_FOR_UNAVAILABLE_RESULT[self.result.availability]
            if self.status is not expected_status:
                raise ValueError("fixture status must match result availability")
        return self


class DeterministicResultCollector:
    """Pure fixture collector used until a permitted official result adapter is connected."""

    def __init__(self, fixtures: tuple[DeterministicResultFixture, ...] = ()) -> None:
        indexed: dict[str, DeterministicResultFixture] = {}
        for fixture in fixtures:
            match_id = fixture.result.match_id if fixture.result is not None else fixture.match_id
            assert match_id is not None
            if match_id in indexed:
                raise ValueError("result fixtures must use distinct match_id values")
            indexed[match_id] = fixture
        self._fixtures = indexed

    def collect(self, request: ResultCollectionRequest) -> ResultCollectionOutcome:
        fixture = self._fixtures.get(request.match_id)
        if fixture is None:
            return _outcome(
                request,
                ResultCollectionStatus.NOT_YET_AVAILABLE,
                "result_not_yet_available",
            )
        if fixture.status is not ResultCollectionStatus.AVAILABLE:
            return _outcome(request, fixture.status, fixture.reason_code)
        result = fixture.result
        assert result is not None
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
        and result.provider_target == request.provider_target
    )


def _outcome(
    request: ResultCollectionRequest,
    status: ResultCollectionStatus,
    reason_code: Identifier | None,
) -> ResultCollectionOutcome:
    assert status is not ResultCollectionStatus.AVAILABLE
    assert reason_code is not None
    return ResultCollectionOutcome(request=request, status=status, reason_code=reason_code)