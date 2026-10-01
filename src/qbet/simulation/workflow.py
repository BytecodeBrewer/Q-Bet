"""Workflow-routed, simulation-only engine evaluation."""

from __future__ import annotations

from decimal import Decimal
from typing import Callable, cast
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from qbet.domain.models import Currency, DomainModel
from qbet.domain.ledger import LedgerCommand, LedgerOperation, PortfolioBalance
from qbet.domain.verification import (
    DomainRiskStatus,
    ProviderState,
    SportsOpportunityRequest,
)
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.layers import OperationalRiskLayer, SimulationLogContext
from qbet.ledger import PortfolioLedger
from qbet.orchestrator import CapitalSnapshot, LiquidityChecker as CapitalLiquidityChecker
from qbet.reporting import CustomerReportInput, SimulationReport
from qbet.request_handler import ModeRequestHandlers
from qbet.simulation.adapters import (
    BonusSimulationAdapter,
    SimulationEngineAdapter,
    SportsCapitalSimulationAdapter,
)
from qbet.simulation.models import SimulationResult, SimulationRunConfig, SimulationStep
from qbet.simulation.reporting import ReportingSimulationRunner
from qbet.simulation.runner import SimulationStepObserver
from qbet.storage import SimulationReportStore
from qbet.storage.bonus_dependencies import PostgresBonusOfferDependencyValidator
from qbet.workflow import (
    LiquidityChecker as WorkflowLiquidityChecker,
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
    customer_report_input: CustomerReportInput | None = None

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
        bonus_dependency_validator: PostgresBonusOfferDependencyValidator,
    ) -> None:
        self._risk_layer = risk_layer
        self._opportunity = opportunity
        self._provider_state = provider_state
        self._log_context = log_context
        self._correlation_id = correlation_id
        self._bonus_dependency_validator = bonus_dependency_validator

    def decide(self, context: WorkflowContext) -> WorkflowStageDecision:
        if isinstance(self._opportunity, BonusEngineRequest):
            dependency = self._bonus_dependency_validator.check(self._opportunity)
            if not dependency.is_current:
                return WorkflowStageDecision(
                    decision=WorkflowDecision.RECHECK,
                    reason=dependency.reason_code or "bonus_offer_dependency_invalid",
                )
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


AuthoritativeLiquidityReserver = Callable[
    [LedgerCommand],
    tuple[PortfolioLedger, WorkflowStageDecision],
]


class _SimulationPortfolioLiquidityChecker:
    """Evaluate and optionally reserve against the authoritative shared sandbox."""

    def __init__(
        self,
        *,
        ledger: Callable[[], PortfolioLedger],
        required_capital: Decimal,
        currency: Currency,
        reserve_command: LedgerCommand | None = None,
        authoritative_reserver: AuthoritativeLiquidityReserver | None = None,
    ) -> None:
        self._ledger = ledger
        self._required_capital = required_capital
        self._currency = currency
        self._reserve_command = reserve_command
        self._authoritative_reserver = authoritative_reserver
        self._checker = CapitalLiquidityChecker()
        self.authoritative_ledger: PortfolioLedger | None = None
        self.reservation_applied = False

    def check(self, context: WorkflowContext) -> WorkflowStageDecision:
        if context.stage is not WorkflowStage.LIQUIDITY_CHECK:
            raise ValueError("simulation portfolio liquidity requires liquidity_check stage")
        balance = self._ledger().balance
        if balance.currency != self._currency:
            return self._checker.capital_decision(
                CapitalSnapshot(
                    available_capital=balance.available,
                    currency=cast(Currency, balance.currency),
                ),
                required_capital=self._required_capital,
                currency=cast(Currency, self._currency),
            )
        if self._authoritative_reserver is not None and self._reserve_command is not None:
            ledger, decision = self._authoritative_reserver(self._reserve_command)
            self.authoritative_ledger = ledger
            self.reservation_applied = decision.decision is WorkflowDecision.ALLOW
            return decision

        return self._checker.capital_decision(
            CapitalSnapshot(
                available_capital=balance.available,
                currency=cast(Currency, balance.currency),
            ),
            required_capital=self._required_capital,
            currency=cast(Currency, self._currency),
        )


class WorkflowSimulationRunner:
    """Routes virtual engine steps through the workflow without execution adapters."""

    def __init__(
        self,
        *,
        liquidity_checker: WorkflowLiquidityChecker | None = None,
        risk_layer: OperationalRiskLayer | None = None,
        report_store: SimulationReportStore | None = None,
        mode_request_handlers: ModeRequestHandlers | None = None,
        simulation_ledger: PortfolioLedger | None = None,
        ledger_writer: Callable[[PortfolioLedger], PortfolioLedger] | None = None,
        liquidity_reserver: AuthoritativeLiquidityReserver | None = None,
        bonus_dependency_validator: PostgresBonusOfferDependencyValidator | None = None,
    ) -> None:
        self._liquidity_checker = liquidity_checker
        self._risk_layer = risk_layer or OperationalRiskLayer()
        self._mode_request_handlers = mode_request_handlers
        self._simulation_ledger = simulation_ledger
        self._ledger_writer = ledger_writer
        self._liquidity_reserver = liquidity_reserver
        self._bonus_dependency_validator = (
            bonus_dependency_validator or PostgresBonusOfferDependencyValidator()
        )
        self._runner = ReportingSimulationRunner(report_store)
        self.last_report: SimulationReport | None = None
        self.last_records = ()
        self.last_ledger: PortfolioLedger | None = None

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
        ledger = self._simulation_ledger or PortfolioLedger(
            balance=PortfolioBalance(
                mode="simulation",
                currency=request.opportunities[0].currency,
                available=request.config.starting_capital,
            )
        )
        pending_amounts: dict[str, Decimal] = {}

        def apply_ledger(step: SimulationStep, operation: LedgerOperation, amount: Decimal) -> bool:
            nonlocal ledger
            updated, decision = ledger.apply(
                LedgerCommand(
                    id=f"{correlation_id}:{step.id}:{operation.value}",
                    dispatch_id=f"{correlation_id}:{step.id}",
                    correlation_id=str(correlation_id),
                    currency=ledger.balance.currency,
                    operation=operation,
                    amount=amount,
                )
            )
            if not decision.accepted:
                return False
            ledger = self._ledger_writer(updated) if self._ledger_writer is not None else updated
            return True

        def route_step(step: SimulationStep) -> bool:
            nonlocal ledger
            assert step.evaluation is not None
            opportunity = opportunities_by_id[step.evaluation.strategy_result.opportunity_id]
            amount = step.evaluation.strategy_result.stake
            reserve_command = (
                LedgerCommand(
                    id=f"{correlation_id}:{step.id}:{LedgerOperation.RESERVE.value}",
                    dispatch_id=f"{correlation_id}:{step.id}",
                    correlation_id=str(correlation_id),
                    currency=ledger.balance.currency,
                    operation=LedgerOperation.RESERVE,
                    amount=amount,
                )
                if amount > 0 and self._liquidity_reserver is not None
                else None
            )
            liquidity_checker = (
                self._liquidity_checker
                or _SimulationPortfolioLiquidityChecker(
                    ledger=lambda: ledger,
                    required_capital=amount,
                    currency=opportunity.currency,
                    reserve_command=reserve_command,
                    authoritative_reserver=self._liquidity_reserver,
                )
            )
            orchestrator = WorkflowOrchestrator(
                {
                    WorkflowStage.DOMAIN_RISK: _RiskStageHandler(
                        self._risk_layer,
                        opportunity,
                        request.provider_state,
                        log_context,
                        correlation_id,
                        self._bonus_dependency_validator,
                    )
                },
                liquidity_checker=liquidity_checker,
                mode_request_handlers=self._mode_request_handlers,
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
                    opportunity_id=opportunity.opportunity_id,
                    payload={"opportunity_id": opportunity.opportunity_id},
                ),
                log_context=log_context,
            )
            reservation_applied = False
            if isinstance(liquidity_checker, _SimulationPortfolioLiquidityChecker):
                if liquidity_checker.authoritative_ledger is not None:
                    ledger = liquidity_checker.authoritative_ledger
                reservation_applied = liquidity_checker.reservation_applied
            workflow_results.append(workflow_result)
            if workflow_result.final_decision is not WorkflowDecision.ALLOW:
                if reservation_applied and not apply_ledger(
                    step,
                    LedgerOperation.RELEASE,
                    amount,
                ):
                    raise ValueError(
                        "simulation liquidity reservation release failed"
                    )
                return False
            if amount == 0:
                return True
            operations = (
                (LedgerOperation.LOCK, LedgerOperation.PENDING)
                if reservation_applied
                else (
                    LedgerOperation.RESERVE,
                    LedgerOperation.LOCK,
                    LedgerOperation.PENDING,
                )
            )
            for operation in operations:
                if not apply_ledger(step, operation, amount):
                    return False
            pending_amounts[step.id] = amount
            return True

        def settle_step(context) -> None:
            step = steps[context.completed_step_count - 1]
            amount = pending_amounts.pop(step.id, None)
            if amount is None:
                return
            payout = max(Decimal(0), amount + step.capital_change)
            if not apply_ledger(step, LedgerOperation.SETTLE, payout):
                raise ValueError("simulation ledger settlement failed")

        simulation_result = self._runner.run(
            request.config,
            steps,
            on_step_completed=on_step_completed,
            on_step_applied=settle_step,
            on_step_ready=route_step,
            log_context=log_context,
            customer_report_input=request.customer_report_input,
        )
        self.last_report = self._runner.last_report
        self.last_records = self._runner.last_records
        self.last_ledger = ledger
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
