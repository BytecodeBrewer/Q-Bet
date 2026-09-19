from __future__ import annotations

from collections.abc import Callable, Mapping
from uuid import UUID

from qbet.layers import SimulationLogContext, SimulationLogRecordType
from qbet.request_handler import ModeRequest, ModeRequestHandlers, RequestHandlerMode
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
from qbet.workflow.routing import RoutedWorkItem, RoutingConfiguration, V1Engine, resolve_routes

RoutingConfigurationLoader = Callable[[], RoutingConfiguration | None]


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
    def route(
        self,
        engine: V1Engine,
        opportunity_id: str,
        correlation_id: UUID,
        owner: str,
    ) -> tuple[RoutedWorkItem, ...]:
        configuration = (
            self._routing_configuration_loader()
            if self._routing_configuration_loader is not None
            else self._routing_configuration
        )
        return self.route_opportunity(
            configuration or RoutingConfiguration(),
            engine,
            opportunity_id,
            correlation_id,
            owner,
        )

    @staticmethod
    def route_opportunity(
        configuration: RoutingConfiguration,
        engine: V1Engine,
        opportunity_id: str,
        correlation_id: UUID,
        owner: str,
    ) -> tuple[RoutedWorkItem, ...]:
        return resolve_routes(configuration, engine, opportunity_id, correlation_id, owner)

    def __init__(
        self,
        stage_handlers: Mapping[WorkflowStage, WorkflowStageHandler] | None = None,
        *,
        liquidity_checker: LiquidityChecker | None = None,
        request_handler: RequestHandler | None = None,
        mode_request_handlers: ModeRequestHandlers | None = None,
        routing_configuration: RoutingConfiguration | None = None,
        routing_configuration_loader: RoutingConfigurationLoader | None = None,
    ) -> None:
        if routing_configuration is not None and routing_configuration_loader is not None:
            raise ValueError(
                "routing configuration and routing configuration loader are mutually exclusive"
            )
        self._stage_handlers = dict(stage_handlers or {})
        self._liquidity_checker = liquidity_checker
        self._request_handler = request_handler
        self._mode_request_handlers = mode_request_handlers
        self._routing_configuration = routing_configuration
        self._routing_configuration_loader = routing_configuration_loader

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
        request_handler_result = None
        for stage in request.stages:
            context = WorkflowContext(request=request, correlation_id=correlation_id, stage=stage)
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
            if self._mode_request_handlers is not None and stage is WorkflowStage.DISPATCH:
                revalidation = self._mode_request_handlers.revalidate(self._mode_request(context))
                decision_name, reason = self._mode_request_handlers.workflow_decision(revalidation)
                mode_decision = WorkflowStageDecision(
                    decision=WorkflowDecision(decision_name), reason=reason
                )
                self._record_transition(
                    transitions,
                    log,
                    correlation_id,
                    stage,
                    WorkflowTransitionKind.REFRESH,
                    mode_decision,
                )
                final_decision = mode_decision.decision
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
            if self._mode_request_handlers is not None and stage is WorkflowStage.DISPATCH:
                request_handler_result = self._mode_request_handlers.retrieve_result(
                    self._mode_request(context)
                )
                log.record(
                    SimulationLogRecordType.EVENT,
                    "request_handler.result",
                    request_handler_result.model_dump(mode="json"),
                )
        return WorkflowResult(
            correlation_id=correlation_id,
            request_id=request.id,
            mode=request.mode,
            final_decision=final_decision,
            transitions=tuple(transitions),
            log_records=log.records,
            request_handler_result=request_handler_result,
        )

    @staticmethod
    def _mode_request(context: WorkflowContext) -> ModeRequest:
        return ModeRequest(
            opportunity_id=context.request.resolved_opportunity_id,
            mode=RequestHandlerMode(context.request.mode.value),
            correlation_id=context.correlation_id,
            lifecycle_id=context.request.id,
            market_revalidation=context.request.market_revalidation,
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
