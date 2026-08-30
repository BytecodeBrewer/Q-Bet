from uuid import UUID
from qbet.layers import SimulationLogRecordType
from qbet.workflow import StaticLiquidityChecker, StaticStageHandler, WorkflowDecision, WorkflowMode, WorkflowOrchestrator, WorkflowRequest, WorkflowStage, WorkflowStageDecision

def request(**changes: object) -> WorkflowRequest:
    values: dict[str, object] = {"id": "workflow-1", "mode": WorkflowMode.SIMULATION, "stages": (WorkflowStage.DATA_AGGREGATION, WorkflowStage.LIQUIDITY_CHECK, WorkflowStage.DISPATCH)}
    values.update(changes)
    return WorkflowRequest(**values)

def test_normal_workflow_records_ordered_correlated_transitions() -> None:
    result = WorkflowOrchestrator().process(request(correlation_id=UUID("12345678-1234-5678-1234-567812345678")))
    assert result.final_decision is WorkflowDecision.ALLOW
    assert [transition.stage for transition in result.transitions] == list(request().stages)
    assert [transition.sequence for transition in result.transitions] == [1, 2, 3]
    assert {record.run_id for record in result.log_records} == {result.correlation_id}
    assert all(record.record_type is SimulationLogRecordType.WORKFLOW_TRANSITION for record in result.log_records)
    assert {record.payload["correlation_id"] for record in result.log_records} == {str(result.correlation_id)}

def test_rejection_prevents_later_stages() -> None:
    result = WorkflowOrchestrator({WorkflowStage.ENGINE_PREPARATION: StaticStageHandler(WorkflowStageDecision(decision=WorkflowDecision.REJECT, reason="invalid input"))}).process(request(stages=(WorkflowStage.DATA_AGGREGATION, WorkflowStage.ENGINE_PREPARATION, WorkflowStage.DISPATCH)))
    assert result.final_decision is WorkflowDecision.REJECT
    assert [transition.stage for transition in result.transitions] == [WorkflowStage.DATA_AGGREGATION, WorkflowStage.ENGINE_PREPARATION]

def test_liquidity_recheck_prevents_dispatch() -> None:
    result = WorkflowOrchestrator(liquidity_checker=StaticLiquidityChecker(WorkflowStageDecision(decision=WorkflowDecision.RECHECK, reason="refresh balance"))).process(request())
    assert result.final_decision is WorkflowDecision.RECHECK
    assert [transition.stage for transition in result.transitions] == [WorkflowStage.DATA_AGGREGATION, WorkflowStage.LIQUIDITY_CHECK]
