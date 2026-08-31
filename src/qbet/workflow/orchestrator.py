from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from qbet.layers import SimulationLogContext, SimulationLogRecordType
from qbet.workflow.models import (
    WorkflowContext,
    WorkflowDecision,
    WorkflowRequest,
    WorkflowResult,
    WorkflowStage,
    WorkflowStageDecision,
    WorkflowTransition,
    WorkflowTransitionKind,
    new_correlation_id,
)
from qbet.workflow.protocol import (
    LiquidityChecker,
    RequestHandler,
    WorkflowStageHandler,
)


class StaticStageHandler:
    def __init__(self, decision: WorkflowStageDecision) -> None:
        self._decision = decision

    def decide(self, context: WorkflowContext) -> WorkflowStageDecision:
        return self._decision


class StaticRequestHandler:
    def __init__(self, decision: WorkflowStageDecision) -> None:
        self._decision = decision

    def refresh(self, context: WorkflowContext) -> WorkflowStageDecision:
        return self._decision


class StaticLiquidityChecker:
    def __init__(self, decision: WorkflowStageDecision) -> None:
        self._decision = decision

    def check(self, context: WorkflowContext) -> WorkflowStageDecision:
        return self._decision


class WorkflowOrchestrator:
    def __init__(
        self,
        stage_handlers: Mapping[WorkflowStage, WorkflowStageHandler] | None = None,
        *,
        liquidity_checker: LiquidityChecker | None = None,
        request_handler: RequestHandler | None = None,
    ) -> None:
        self._stage_handlers = dict(stage_handlers or {})
        self._liquidity_checker = liquidity_checker
        # This ticket defines the refresh seam; later workflow stages invoke it explicitly.
        self._request_handler = request_handler

    def process(
        self,
        request: WorkflowRequest,
        *,
        log_context: SimulationLogContext | None = None,
    ) -> WorkflowResult:
        correlation_id = request.correlation_id or new_correlation_id()
        if log_context is not None and log_context.run_id != correlation_id:
            raise ValueError("log context and workflow correlation_id must match")
        log = log_context or SimulationLogContext(run_id=correlation_id)
        transitions: list[WorkflowTransition] = []
        final_decision = WorkflowDecision.ALLOW
        for stage in request.stages:
            context = WorkflowContext(
                request=request, correlation_id=correlation_id, stage=stage
            )
            request_handler = self._request_handler
            if request_handler is not None and stage in {
                WorkflowStage.DOMAIN_RISK,
                WorkflowStage.LIQUIDITY_CHECK,
                WorkflowStage.DISPATCH,
            }:
                refresh_decision = request_handler.refresh(context)
                self._record_transition(
                    transitions,
                    log,
                    correlation_id,
                    stage,
                    WorkflowTransitionKind.REFRESH,
                    refresh_decision,
                )
                final_decision = refresh_decision.decision
                if final_decision is not WorkflowDecision.ALLOW:
                    break
            decision = self._decide(context)
            self._record_transition(
                transitions,
                log,
                correlation_id,
                stage,
                WorkflowTransitionKind.STAGE,
                decision,
            )
            final_decision = decision.decision
            if final_decision is not WorkflowDecision.ALLOW:
                break
        return WorkflowResult(
            correlation_id=correlation_id,
            mode=request.mode,
            final_decision=final_decision,
            transitions=tuple(transitions),
            log_records=log.records,
        )

    @staticmethod
    def _record_transition(
        transitions: list[WorkflowTransition],
        log: SimulationLogContext,
        correlation_id: UUID,
        stage: WorkflowStage,
        kind: WorkflowTransitionKind,
        decision: WorkflowStageDecision,
    ) -> None:
        transition = WorkflowTransition(
            sequence=len(transitions) + 1,
            correlation_id=correlation_id,
            stage=stage,
            kind=kind,
            decision=decision.decision,
            reason=decision.reason,
        )
        transitions.append(transition)
        log.record(
            SimulationLogRecordType.WORKFLOW_TRANSITION,
            "workflow.orchestrator",
            transition.model_dump(mode="json"),
        )

    def _decide(self, context: WorkflowContext) -> WorkflowStageDecision:
        if context.stage is WorkflowStage.LIQUIDITY_CHECK and self._liquidity_checker:
            return self._liquidity_checker.check(context)
        handler = self._stage_handlers.get(context.stage)
        return (
            handler.decide(context)
            if handler
            else WorkflowStageDecision(decision=WorkflowDecision.ALLOW)
        )
