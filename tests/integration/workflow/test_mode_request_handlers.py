from datetime import UTC, datetime
from uuid import UUID

import pytest

from qbet.request_handler import (
    ExecutionSandboxRequestHandler,
    ModeRequestHandlers,
    RequestHandlerMode,
    ResultStatus,
    RevalidationOutcome,
    SandboxResultFixture,
    SandboxRevalidationFixture,
    SimulationSandboxRequestHandler,
)
from qbet.workflow import (
    WorkflowDecision,
    WorkflowMode,
    WorkflowOrchestrator,
    WorkflowRequest,
    WorkflowStage,
    WorkflowTransitionKind,
)

_TIMESTAMP = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
_CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def workflow_request(mode: WorkflowMode, opportunity_id: str) -> WorkflowRequest:
    return WorkflowRequest(
        id=f"{mode.value}-lifecycle-1",
        opportunity_id=opportunity_id,
        mode=mode,
        correlation_id=_CORRELATION_ID,
        stages=(WorkflowStage.LIQUIDITY_CHECK, WorkflowStage.DISPATCH),
    )


def handlers(
    *,
    simulation_outcome: RevalidationOutcome = RevalidationOutcome.VALID,
    execution_outcome: RevalidationOutcome = RevalidationOutcome.VALID,
    simulation_status: ResultStatus = ResultStatus.SUCCESS,
    execution_status: ResultStatus = ResultStatus.SUCCESS,
) -> ModeRequestHandlers:
    def revalidation(opportunity_id: str, outcome: RevalidationOutcome) -> SandboxRevalidationFixture:
        return SandboxRevalidationFixture(
            opportunity_id=opportunity_id,
            outcome=outcome,
            validated_at=_TIMESTAMP,
            reason_code=None if outcome is RevalidationOutcome.VALID else f"{outcome.value}_fixture",
        )

    def result(opportunity_id: str, status: ResultStatus) -> SandboxResultFixture:
        return SandboxResultFixture(
            opportunity_id=opportunity_id,
            status=status,
            observed_at=_TIMESTAMP,
            reason_code=None if status is ResultStatus.SUCCESS else f"{status.value}_fixture",
            result_reference="result-1" if status is ResultStatus.SUCCESS else None,
        )

    return ModeRequestHandlers(
        simulation=SimulationSandboxRequestHandler(
            revalidation_fixtures=(revalidation("simulation-opportunity", simulation_outcome),),
            result_fixtures=(result("simulation-opportunity", simulation_status),),
        ),
        execution=ExecutionSandboxRequestHandler(
            revalidation_fixtures=(revalidation("execution-opportunity", execution_outcome),),
            result_fixtures=(result("execution-opportunity", execution_status),),
        ),
    )


def test_mode_specific_handlers_revalidate_before_dispatch_and_return_result() -> None:
    result = WorkflowOrchestrator(mode_request_handlers=handlers()).process(
        workflow_request(WorkflowMode.SIMULATION, "simulation-opportunity")
    )

    assert result.final_decision is WorkflowDecision.ALLOW
    assert result.request_handler_result is not None
    assert result.request_handler_result.status is ResultStatus.SUCCESS
    assert result.request_handler_result.mode is RequestHandlerMode.SIMULATION
    assert result.request_handler_result.correlation_id == _CORRELATION_ID
    assert [(transition.kind, transition.stage) for transition in result.transitions] == [
        (WorkflowTransitionKind.STAGE, WorkflowStage.LIQUIDITY_CHECK),
        (WorkflowTransitionKind.REFRESH, WorkflowStage.DISPATCH),
        (WorkflowTransitionKind.STAGE, WorkflowStage.DISPATCH),
    ]


@pytest.mark.parametrize(
    ("outcome", "decision"),
    [
        (RevalidationOutcome.CHANGED, WorkflowDecision.RECHECK),
        (RevalidationOutcome.UNAVAILABLE, WorkflowDecision.RECHECK),
        (RevalidationOutcome.EXPIRED, WorkflowDecision.REJECT),
        (RevalidationOutcome.REJECTED, WorkflowDecision.REJECT),
    ],
)
def test_non_valid_revalidation_blocks_dispatch(
    outcome: RevalidationOutcome, decision: WorkflowDecision
) -> None:
    result = WorkflowOrchestrator(
        mode_request_handlers=handlers(simulation_outcome=outcome)
    ).process(workflow_request(WorkflowMode.SIMULATION, "simulation-opportunity"))

    assert result.final_decision is decision
    assert result.request_handler_result is None
    assert [(transition.kind, transition.stage) for transition in result.transitions] == [
        (WorkflowTransitionKind.STAGE, WorkflowStage.LIQUIDITY_CHECK),
        (WorkflowTransitionKind.REFRESH, WorkflowStage.DISPATCH),
    ]


def test_execution_uses_its_own_handler_and_result_state() -> None:
    result = WorkflowOrchestrator(
        mode_request_handlers=handlers(
            simulation_outcome=RevalidationOutcome.REJECTED,
            execution_status=ResultStatus.PARTIAL,
        )
    ).process(workflow_request(WorkflowMode.EXECUTION, "execution-opportunity"))

    assert result.final_decision is WorkflowDecision.ALLOW
    assert result.request_handler_result is not None
    assert result.request_handler_result.mode is RequestHandlerMode.EXECUTION
    assert result.request_handler_result.status is ResultStatus.PARTIAL


def test_router_requires_distinct_mode_handler_instances() -> None:
    handler = SimulationSandboxRequestHandler()

    with pytest.raises(ValueError, match="separate request handler instances"):
        ModeRequestHandlers(simulation=handler, execution=handler)
