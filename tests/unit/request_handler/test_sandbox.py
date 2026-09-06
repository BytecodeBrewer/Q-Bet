from datetime import UTC, datetime
from uuid import UUID

import pytest

from qbet.request_handler import (
    ExecutionSandboxRequestHandler,
    ModeRequest,
    RequestHandlerMode,
    ResultStatus,
    RevalidationOutcome,
    SandboxResultFixture,
    SandboxRevalidationFixture,
    SimulationSandboxRequestHandler,
)

_TIMESTAMP = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def request(mode: RequestHandlerMode = RequestHandlerMode.SIMULATION) -> ModeRequest:
    return ModeRequest(
        opportunity_id="opportunity-1",
        mode=mode,
        correlation_id=UUID("12345678-1234-5678-1234-567812345678"),
        lifecycle_id="lifecycle-1",
    )


def test_simulation_sandbox_returns_fixture_driven_valid_revalidation_and_result() -> None:
    handler = SimulationSandboxRequestHandler(
        revalidation_fixtures=(
            SandboxRevalidationFixture(
                opportunity_id="opportunity-1",
                outcome=RevalidationOutcome.VALID,
                validated_at=_TIMESTAMP,
            ),
        ),
        result_fixtures=(
            SandboxResultFixture(
                opportunity_id="opportunity-1",
                status=ResultStatus.SUCCESS,
                observed_at=_TIMESTAMP,
                result_reference="result-1",
            ),
        ),
    )

    revalidation = handler.revalidate(request())
    result = handler.retrieve_result(request())

    assert revalidation.is_valid
    assert revalidation.correlation_id == request().correlation_id
    assert result.status is ResultStatus.SUCCESS
    assert result.lifecycle_id == "lifecycle-1"


@pytest.mark.parametrize(
    ("outcome", "reason_code"),
    [
        (RevalidationOutcome.CHANGED, "odds_changed"),
        (RevalidationOutcome.EXPIRED, "opportunity_expired"),
        (RevalidationOutcome.UNAVAILABLE, "provider_unavailable"),
        (RevalidationOutcome.REJECTED, "not_profitable"),
    ],
)
def test_sandbox_supports_every_non_valid_revalidation_outcome(
    outcome: RevalidationOutcome, reason_code: str
) -> None:
    handler = SimulationSandboxRequestHandler(
        revalidation_fixtures=(
            SandboxRevalidationFixture(
                opportunity_id="opportunity-1",
                outcome=outcome,
                validated_at=_TIMESTAMP,
                reason_code=reason_code,
            ),
        )
    )

    result = handler.revalidate(request())

    assert result.outcome is outcome
    assert result.reason_code == reason_code


def test_missing_fixture_is_deterministic_and_blocks_revalidation() -> None:
    handler = SimulationSandboxRequestHandler()

    first = handler.revalidate(request())
    second = handler.revalidate(request())

    assert first == second
    assert first.outcome is RevalidationOutcome.UNAVAILABLE
    assert first.reason_code == "revalidation_fixture_unavailable"


@pytest.mark.parametrize(
    "status",
    [
        ResultStatus.FAILED,
        ResultStatus.CANCELLED,
        ResultStatus.PARTIAL,
        ResultStatus.UNKNOWN,
        ResultStatus.NOT_YET_AVAILABLE,
    ],
)
def test_sandbox_result_retrieval_distinguishes_non_success_states(status: ResultStatus) -> None:
    handler = SimulationSandboxRequestHandler(
        result_fixtures=(
            SandboxResultFixture(
                opportunity_id="opportunity-1",
                status=status,
                observed_at=_TIMESTAMP,
                reason_code=f"{status.value}_result",
            ),
        )
    )

    result = handler.retrieve_result(request())

    assert result.status is status
    assert result.reason_code == f"{status.value}_result"


def test_handlers_reject_the_other_mode_without_sharing_state() -> None:
    simulation = SimulationSandboxRequestHandler()
    execution = ExecutionSandboxRequestHandler()

    with pytest.raises(ValueError, match="mode mismatch"):
        simulation.revalidate(request(RequestHandlerMode.EXECUTION))
    with pytest.raises(ValueError, match="mode mismatch"):
        execution.revalidate(request())
