"""Deterministic sandbox request handlers with no provider or capital side effects."""

from __future__ import annotations

from datetime import UTC, datetime

from qbet.request_handler.models import (
    ModeRequest,
    RequestHandlerMode,
    RequestHandlerResult,
    ResultStatus,
    RevalidationOutcome,
    RevalidationResult,
    SandboxResultFixture,
    SandboxRevalidationFixture,
)

_DEFAULT_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)


class _SandboxRequestHandler:
    def __init__(
        self,
        mode: RequestHandlerMode,
        *,
        revalidation_fixtures: tuple[SandboxRevalidationFixture, ...] = (),
        result_fixtures: tuple[SandboxResultFixture, ...] = (),
    ) -> None:
        self._mode = mode
        self._revalidations = _revalidations_by_opportunity_id(revalidation_fixtures)
        self._results = _results_by_opportunity_id(result_fixtures)

    def revalidate(self, request: ModeRequest) -> RevalidationResult:
        self._requires_matching_mode(request)
        fixture = self._revalidations.get(request.opportunity_id)
        if fixture is None:
            return RevalidationResult(
                **request.model_dump(),
                validated_at=_DEFAULT_TIMESTAMP,
                outcome=RevalidationOutcome.UNAVAILABLE,
                reason_code="revalidation_fixture_unavailable",
            )
        return RevalidationResult(
            **request.model_dump(),
            validated_at=fixture.validated_at,
            outcome=fixture.outcome,
            reason_code=fixture.reason_code,
        )

    def retrieve_result(self, request: ModeRequest) -> RequestHandlerResult:
        self._requires_matching_mode(request)
        fixture = self._results.get(request.opportunity_id)
        if fixture is None:
            return RequestHandlerResult(
                **request.model_dump(),
                observed_at=_DEFAULT_TIMESTAMP,
                status=ResultStatus.NOT_YET_AVAILABLE,
                reason_code="result_fixture_not_available",
            )
        return RequestHandlerResult(
            **request.model_dump(),
            observed_at=fixture.observed_at,
            status=fixture.status,
            reason_code=fixture.reason_code,
            result_reference=fixture.result_reference,
        )

    def _requires_matching_mode(self, request: ModeRequest) -> None:
        if request.mode is not self._mode:
            raise ValueError("request handler mode mismatch")


class SimulationSandboxRequestHandler(_SandboxRequestHandler):
    def __init__(
        self,
        *,
        revalidation_fixtures: tuple[SandboxRevalidationFixture, ...] = (),
        result_fixtures: tuple[SandboxResultFixture, ...] = (),
    ) -> None:
        super().__init__(
            RequestHandlerMode.SIMULATION,
            revalidation_fixtures=revalidation_fixtures,
            result_fixtures=result_fixtures,
        )


class ExecutionSandboxRequestHandler(_SandboxRequestHandler):
    def __init__(
        self,
        *,
        revalidation_fixtures: tuple[SandboxRevalidationFixture, ...] = (),
        result_fixtures: tuple[SandboxResultFixture, ...] = (),
    ) -> None:
        super().__init__(
            RequestHandlerMode.EXECUTION,
            revalidation_fixtures=revalidation_fixtures,
            result_fixtures=result_fixtures,
        )


def _revalidations_by_opportunity_id(
    fixtures: tuple[SandboxRevalidationFixture, ...],
) -> dict[str, SandboxRevalidationFixture]:
    values = {fixture.opportunity_id: fixture for fixture in fixtures}
    if len(values) != len(fixtures):
        raise ValueError("sandbox fixtures must use distinct opportunity_id values")
    return values


def _results_by_opportunity_id(
    fixtures: tuple[SandboxResultFixture, ...],
) -> dict[str, SandboxResultFixture]:
    values = {fixture.opportunity_id: fixture for fixture in fixtures}
    if len(values) != len(fixtures):
        raise ValueError("sandbox fixtures must use distinct opportunity_id values")
    return values
