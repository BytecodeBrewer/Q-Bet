from uuid import UUID

import pytest
from pydantic import ValidationError

from qbet.layers import SimulationLogRecordType
from qbet.workflow import (
    StaticLiquidityChecker,
    StaticStageHandler,
    WorkflowContext,
    WorkflowDecision,
    WorkflowMode,
    WorkflowOrchestrator,
    WorkflowRequest,
    WorkflowStage,
    WorkflowStageDecision,
    WorkflowTransitionKind,
)


class RecordingRequestHandler:
    def __init__(self, decisions: dict[WorkflowStage, WorkflowStageDecision]) -> None:
        self._decisions = decisions
        self.contexts: list[WorkflowContext] = []

    def refresh(self, context: WorkflowContext) -> WorkflowStageDecision:
        self.contexts.append(context)
        return self._decisions.get(
            context.stage, WorkflowStageDecision(decision=WorkflowDecision.ALLOW)
        )


def request(**changes: object) -> WorkflowRequest:
    values: dict[str, object] = {
        "id": "workflow-1",
        "mode": WorkflowMode.SIMULATION,
        "stages": (
            WorkflowStage.DATA_AGGREGATION,
            WorkflowStage.LIQUIDITY_CHECK,
            WorkflowStage.DISPATCH,
        ),
    }
    values.update(changes)
    return WorkflowRequest.model_validate(values)


def test_normal_workflow_records_ordered_correlated_transitions() -> None:
    result = WorkflowOrchestrator().process(
        request(correlation_id=UUID("12345678-1234-5678-1234-567812345678"))
    )
    assert result.final_decision is WorkflowDecision.ALLOW
    assert [transition.stage for transition in result.transitions] == list(
        request().stages
    )
    assert [transition.sequence for transition in result.transitions] == [1, 2, 3]
    assert {record.run_id for record in result.log_records} == {result.correlation_id}
    assert all(
        record.record_type is SimulationLogRecordType.WORKFLOW_TRANSITION
        for record in result.log_records
    )
    assert {record.payload["correlation_id"] for record in result.log_records} == {
        str(result.correlation_id)
    }


def test_request_handler_refreshes_controlled_stages_in_order() -> None:
    correlation_id = UUID("12345678-1234-5678-1234-567812345678")
    handler = RecordingRequestHandler({})
    result = WorkflowOrchestrator(request_handler=handler).process(
        request(
            correlation_id=correlation_id,
            stages=(
                WorkflowStage.DATA_AGGREGATION,
                WorkflowStage.ENGINE_PREPARATION,
                WorkflowStage.CALCULATION,
                WorkflowStage.DOMAIN_RISK,
                WorkflowStage.LIQUIDITY_CHECK,
                WorkflowStage.DISPATCH,
            ),
        )
    )

    assert [context.stage for context in handler.contexts] == [
        WorkflowStage.DOMAIN_RISK,
        WorkflowStage.LIQUIDITY_CHECK,
        WorkflowStage.DISPATCH,
    ]
    assert {context.correlation_id for context in handler.contexts} == {correlation_id}
    assert [
        (transition.kind, transition.stage) for transition in result.transitions
    ] == [
        (WorkflowTransitionKind.STAGE, WorkflowStage.DATA_AGGREGATION),
        (WorkflowTransitionKind.STAGE, WorkflowStage.ENGINE_PREPARATION),
        (WorkflowTransitionKind.STAGE, WorkflowStage.CALCULATION),
        (WorkflowTransitionKind.REFRESH, WorkflowStage.DOMAIN_RISK),
        (WorkflowTransitionKind.STAGE, WorkflowStage.DOMAIN_RISK),
        (WorkflowTransitionKind.REFRESH, WorkflowStage.LIQUIDITY_CHECK),
        (WorkflowTransitionKind.STAGE, WorkflowStage.LIQUIDITY_CHECK),
        (WorkflowTransitionKind.REFRESH, WorkflowStage.DISPATCH),
        (WorkflowTransitionKind.STAGE, WorkflowStage.DISPATCH),
    ]
    refresh_records = [
        record
        for record in result.log_records
        if record.payload["kind"] == WorkflowTransitionKind.REFRESH.value
    ]
    assert len(refresh_records) == 3
    assert {record.payload["correlation_id"] for record in refresh_records} == {
        str(correlation_id)
    }


def test_request_handler_refreshes_execution_workflow_without_side_effects() -> None:
    correlation_id = UUID("87654321-4321-8765-4321-876543218765")
    handler = RecordingRequestHandler({})
    result = WorkflowOrchestrator(request_handler=handler).process(
        request(
            mode=WorkflowMode.EXECUTION,
            correlation_id=correlation_id,
            stages=(
                WorkflowStage.DOMAIN_RISK,
                WorkflowStage.LIQUIDITY_CHECK,
                WorkflowStage.DISPATCH,
            ),
        )
    )

    assert result.mode is WorkflowMode.EXECUTION
    assert [(context.request.mode, context.stage) for context in handler.contexts] == [
        (WorkflowMode.EXECUTION, WorkflowStage.DOMAIN_RISK),
        (WorkflowMode.EXECUTION, WorkflowStage.LIQUIDITY_CHECK),
        (WorkflowMode.EXECUTION, WorkflowStage.DISPATCH),
    ]
    assert {context.correlation_id for context in handler.contexts} == {correlation_id}
    assert [
        (transition.kind, transition.stage) for transition in result.transitions
    ] == [
        (WorkflowTransitionKind.REFRESH, WorkflowStage.DOMAIN_RISK),
        (WorkflowTransitionKind.STAGE, WorkflowStage.DOMAIN_RISK),
        (WorkflowTransitionKind.REFRESH, WorkflowStage.LIQUIDITY_CHECK),
        (WorkflowTransitionKind.STAGE, WorkflowStage.LIQUIDITY_CHECK),
        (WorkflowTransitionKind.REFRESH, WorkflowStage.DISPATCH),
        (WorkflowTransitionKind.STAGE, WorkflowStage.DISPATCH),
    ]


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        (WorkflowDecision.REJECT, "source unavailable"),
        (WorkflowDecision.RECHECK, "odds changed"),
    ],
)
def test_request_handler_stop_decision_prevents_affected_and_later_stages(
    decision: WorkflowDecision, reason: str
) -> None:
    handler = RecordingRequestHandler(
        {
            WorkflowStage.LIQUIDITY_CHECK: WorkflowStageDecision(
                decision=decision, reason=reason
            )
        }
    )
    result = WorkflowOrchestrator(request_handler=handler).process(
        request(
            stages=(
                WorkflowStage.DOMAIN_RISK,
                WorkflowStage.LIQUIDITY_CHECK,
                WorkflowStage.DISPATCH,
            )
        )
    )

    assert result.final_decision is decision
    assert [context.stage for context in handler.contexts] == [
        WorkflowStage.DOMAIN_RISK,
        WorkflowStage.LIQUIDITY_CHECK,
    ]
    assert [
        (transition.kind, transition.stage, transition.decision)
        for transition in result.transitions
    ] == [
        (
            WorkflowTransitionKind.REFRESH,
            WorkflowStage.DOMAIN_RISK,
            WorkflowDecision.ALLOW,
        ),
        (
            WorkflowTransitionKind.STAGE,
            WorkflowStage.DOMAIN_RISK,
            WorkflowDecision.ALLOW,
        ),
        (WorkflowTransitionKind.REFRESH, WorkflowStage.LIQUIDITY_CHECK, decision),
    ]
    assert result.transitions[-1].reason == reason


def test_rejection_prevents_later_stages() -> None:
    result = WorkflowOrchestrator(
        {
            WorkflowStage.ENGINE_PREPARATION: StaticStageHandler(
                WorkflowStageDecision(
                    decision=WorkflowDecision.REJECT, reason="invalid input"
                )
            )
        }
    ).process(
        request(
            stages=(
                WorkflowStage.DATA_AGGREGATION,
                WorkflowStage.ENGINE_PREPARATION,
                WorkflowStage.LIQUIDITY_CHECK,
                WorkflowStage.DISPATCH,
            )
        )
    )
    assert result.final_decision is WorkflowDecision.REJECT
    assert [transition.stage for transition in result.transitions] == [
        WorkflowStage.DATA_AGGREGATION,
        WorkflowStage.ENGINE_PREPARATION,
    ]


def test_liquidity_recheck_prevents_dispatch() -> None:
    result = WorkflowOrchestrator(
        liquidity_checker=StaticLiquidityChecker(
            WorkflowStageDecision(
                decision=WorkflowDecision.RECHECK, reason="refresh balance"
            )
        )
    ).process(request())
    assert result.final_decision is WorkflowDecision.RECHECK
    assert [transition.stage for transition in result.transitions] == [
        WorkflowStage.DATA_AGGREGATION,
        WorkflowStage.LIQUIDITY_CHECK,
    ]


@pytest.mark.parametrize(
    "stages",
    [
        (WorkflowStage.DISPATCH,),
        (WorkflowStage.DISPATCH, WorkflowStage.LIQUIDITY_CHECK),
        (
            WorkflowStage.DATA_AGGREGATION,
            WorkflowStage.LIQUIDITY_CHECK,
            WorkflowStage.LIQUIDITY_CHECK,
        ),
    ],
)
def test_invalid_routes_cannot_bypass_liquidity_or_pipeline_order(
    stages: tuple[WorkflowStage, ...],
) -> None:
    with pytest.raises(ValidationError):
        request(stages=stages)
