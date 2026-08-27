from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from pydantic import Field, model_validator
from qbet.calculations import DutchingInput, DutchingResult, TwoWayArbitrageInput, TwoWayArbitrageResult, calculate_dutching, calculate_two_way_arbitrage
from qbet.domain.models import Currency, DomainModel, ExecutionPlan, ExecutionStatus, ExecutionStep, Identifier, StrategyResult
from qbet.engines.protocol import StrategyEngine
class SportsCapitalEngineRequest(DomainModel):
    opportunity_id: Identifier
    inputs: TwoWayArbitrageInput | DutchingInput
    currency: Currency
    execution_offer_ids: tuple[Identifier, ...] = Field(min_length=2)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    execution_plan_id: UUID = Field(default_factory=uuid4)
    @model_validator(mode="after")
    def valid_request(self):
        expected = len(self.inputs.offers) if isinstance(self.inputs, DutchingInput) else 2
        if len(self.execution_offer_ids) != expected or len(set(self.execution_offer_ids)) != expected: raise ValueError("execution_offer_ids must match strategy outcomes")
        offer_currency = self.inputs.first_offer.currency if isinstance(self.inputs, TwoWayArbitrageInput) else self.inputs.offers[0].currency
        if self.currency != offer_currency: raise ValueError("request currency must match calculation input currency")
        return self
class SportsCapitalEngineEvaluation(DomainModel):
    calculation_result: TwoWayArbitrageResult | DutchingResult
    strategy_result: StrategyResult
    worst_case_profit_loss: Decimal
    is_profitable: bool
    execution_plan: ExecutionPlan
    @model_validator(mode="after")
    def approval_only_plan(self):
        if not self.execution_plan.requires_approval: raise ValueError("execution plans must require approval")
        if any(step.status is not ExecutionStatus.REQUIRES_APPROVAL for step in self.execution_plan.steps): raise ValueError("execution steps must require approval")
        if self.execution_plan.strategy_result != self.strategy_result: raise ValueError("execution plan must use the evaluation strategy result")
        return self
class SportsCapitalEngine(StrategyEngine[SportsCapitalEngineRequest, SportsCapitalEngineEvaluation]):
    def evaluate(self, request):
        result = calculate_two_way_arbitrage(request.inputs) if isinstance(request.inputs, TwoWayArbitrageInput) else calculate_dutching(request.inputs)
        stakes = (result.first_stake, result.second_stake) if isinstance(result, TwoWayArbitrageResult) else tuple(a.stake for a in result.allocations)
        worst = result.guaranteed_profit_loss if isinstance(result, TwoWayArbitrageResult) else result.worst_case_profit_loss
        strategy_result = StrategyResult(strategy="two_way_arbitrage" if isinstance(result, TwoWayArbitrageResult) else "dutching", opportunity_id=request.opportunity_id, stake=sum(stakes, Decimal("0")), expected_profit=worst, currency=request.currency, generated_at=request.generated_at)
        plan = ExecutionPlan(id=request.execution_plan_id, strategy_result=strategy_result, steps=tuple(ExecutionStep(offer_id=offer, stake=stake, status=ExecutionStatus.REQUIRES_APPROVAL) for offer, stake in zip(request.execution_offer_ids, stakes, strict=True)), created_at=request.generated_at, requires_approval=True)
        return SportsCapitalEngineEvaluation(calculation_result=result, strategy_result=strategy_result, worst_case_profit_loss=worst, is_profitable=worst >= Decimal("0"), execution_plan=plan)