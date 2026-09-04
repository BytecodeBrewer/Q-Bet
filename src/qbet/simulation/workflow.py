"""Workflow-routed, simulation-only engine evaluation."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field, model_validator

from qbet.domain.models import DomainModel
from qbet.domain.verification import (
    DomainRiskStatus,
    ProviderState,
    SportsOpportunityRequest,
)
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.layers import OperationalRiskLayer, SimulationLogContext
from qbet.reporting import SimulationReport
from qbet.simulation.adapters import (
    BonusSimulationAdapter,
    SimulationEngineAdapter,
    SportsCapitalSimulationAdapter,
)
from qbet.simulation.models import SimulationResult, SimulationRunConfig, SimulationStep
from qbet.simulation.reporting import ReportingSimulationRunner
from qbet.simulation.runner import SimulationStepObserver
from qbet.storage import SimulationReportStore
from qbet.workflow import (
    LiquidityChecker,
    StaticLiquidityChecker,
    WorkflowContext,
    WorkflowDecision,
    WorkflowMode,
    WorkflowOrchestrator,
    WorkflowRequest,
    WorkflowResult,
    WorkflowStage,
    WorkflowStageDecision,
    WorkflowStageHandler,
)

WorkflowSimulationOpportunity = BonusEngineRequest | SportsCapitalEngineRequest


class WorkflowSimulationRequest(DomainModel):
    config: SimulationRunConfig
    opportunities: tuple[WorkflowSimulationOpportunity, ...] = Field(min_length=1)
    provider_state: ProviderState
    correlation_id: UUID | None = None

    @model_validator(mode="after")
    def opportunities_match_simulation_engine(self) -> "WorkflowSimulationRequest":
        if self.config.engine.value == "bonus" and not all(
            isinstance(opportunity, BonusEngineRequest) for opportunity in self.opportunities
        ):
            raise ValueError("bonus simulations require BonusEngineRequest opportunities")
        if self.config.engine.value == "sports_capital" and not all(
            isinstance(opportunity, SportsCapitalEngineRequest)
            for opportunity in self.opportunities
        ):
            raise ValueError(
                "sports capital simulations require SportsCapitalEngineRequest opportunities"
            )
        return self


class WorkflowSimulationResult(DomainModel):
    correlation_id: UUID
    simulation_result: SimulationResult
    workflow_results: tuple[WorkflowResult, ...]


class _RiskStageHandler(WorkflowStageHandler):
    def __init__(
        self,
        risk_layer: OperationalRiskLayer,
        opportunity: SportsOpportunityRequest,
        provider_state: ProviderState,
        log_context: SimulationLogContext,
        correlation_id: UUID,
    ) -> None:
        self._risk_layer = risk_layer
        self._opportunity = opportunity
        self._provider_state = provider_state
        self._log_context = log_context
        self._correlation_id = correlation_id

    def decide(self, context: WorkflowContext) -> WorkflowStageDecision:
        result = self._risk_layer.verify_opportunity(
            self._opportunity,
            self._provider_state,
            log_context=self._log_context,
            correlation_id=self._correlation_id,
        )
        if result.status is DomainRiskStatus.REJECT:
            return WorkflowStageDecision(
                decision=WorkflowDecision.REJECT, reason=result.decision_code.value
            )
        if result.status is DomainRiskStatus.RECHECK:
            return WorkflowStageDecision(
                decision=WorkflowDecision.RECHECK, reason=result.decision_code.value
            )
        return WorkflowStageDecision(decision=WorkflowDecision.ALLOW)


class WorkflowSimulationRunner:
    """Routes virtual engine steps through the workflow without execution adapters."""

    def __init__(
        self,
        *,
        liquidity_checker: LiquidityChecker | None = None,
        risk_layer: OperationalRiskLayer | None = None,
        report_store: SimulationReportStore | None = None,
    ) -> None:
        self._liquidity_checker = liquidity_checker or StaticLiquidityChecker(
            WorkflowStageDecision(decision=WorkflowDecision.ALLOW)
        )
        self._risk_layer = risk_layer or OperationalRiskLayer()
        self._runner = ReportingSimulationRunner(report_store)
        self.last_report: SimulationReport | None = None
        self.last_records = ()

    def request_stop(self) -> None:
        self._runner.request_stop()

    def run(
        self,
        request: WorkflowSimulationRequest,
        *,
        on_step_completed: SimulationStepObserver | None = None,
    ) -> WorkflowSimulationResult:
        correlation_id = request.correlation_id or uuid4()
        log_context = self._runner.log_context(correlation_id)
        steps = self._adapter_for(request).build_steps(request.config)
        opportunities_by_id = {
            opportunity.opportunity_id: opportunity for opportunity in request.opportunities
        }
        workflow_results: list[WorkflowResult] = []

        def route_step(step: SimulationStep) -> bool:
            assert step.evaluation is not None
            opportunity = opportunities_by_id[step.evaluation.strategy_result.opportunity_id]
            orchestrator = WorkflowOrchestrator(
                {
                    WorkflowStage.DOMAIN_RISK: _RiskStageHandler(
                        self._risk_layer,
                        opportunity,
                        request.provider_state,
                        log_context,
                        correlation_id,
                    )
                },
                liquidity_checker=self._liquidity_checker,
            )
            workflow_result = orchestrator.process(
                WorkflowRequest(
                    id=step.id,
                    mode=WorkflowMode.SIMULATION,
                    stages=(
                        WorkflowStage.DATA_AGGREGATION,
                        WorkflowStage.ENGINE_PREPARATION,
                        WorkflowStage.CALCULATION,
                        WorkflowStage.DOMAIN_RISK,
                        WorkflowStage.LIQUIDITY_CHECK,
                        WorkflowStage.DISPATCH,
                    ),
                    correlation_id=correlation_id,
                    payload={"opportunity_id": opportunity.opportunity_id},
                ),
                log_context=log_context,
            )
            workflow_results.append(workflow_result)
            return workflow_result.final_decision is WorkflowDecision.ALLOW

        simulation_result = self._runner.run(
            request.config,
            steps,
            on_step_completed=on_step_completed,
            on_step_ready=route_step,
            log_context=log_context,
        )
        self.last_report = self._runner.last_report
        self.last_records = self._runner.last_records
        return WorkflowSimulationResult(
            correlation_id=correlation_id,
            simulation_result=simulation_result,
            workflow_results=tuple(workflow_results),
        )

    @staticmethod
    def _adapter_for(request: WorkflowSimulationRequest) -> SimulationEngineAdapter:
        if request.config.engine.value == "bonus":
            requests = tuple(
                opportunity
                for opportunity in request.opportunities
                if isinstance(opportunity, BonusEngineRequest)
            )
            if len(requests) != len(request.opportunities):
                raise ValueError("bonus simulations require BonusEngineRequest opportunities")
            return BonusSimulationAdapter(requests)
        if request.config.engine.value == "sports_capital":
            requests = tuple(
                opportunity
                for opportunity in request.opportunities
                if isinstance(opportunity, SportsCapitalEngineRequest)
            )
            if len(requests) != len(request.opportunities):
                raise ValueError(
                    "sports capital simulations require SportsCapitalEngineRequest opportunities"
                )
            return SportsCapitalSimulationAdapter(requests)
        raise ValueError("workflow simulation only supports concrete v1 engines")
