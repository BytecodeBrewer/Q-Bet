from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from qbet.calculations import (
    FreeBetInput,
    FreeBetResult,
    QualifyingBetInput,
    QualifyingBetResult,
    calculate_free_bet,
    calculate_qualifying_bet,
)
from qbet.domain.models import (
    Currency,
    DomainModel,
    ExecutionPlan,
    ExecutionStatus,
    ExecutionStep,
    Identifier,
    StrategyResult,
)
from qbet.engines.protocol import StrategyEngine


class BonusStrategy(str):
    pass


class BonusEngineRequest(DomainModel):
    opportunity_id: Identifier
    inputs: QualifyingBetInput | FreeBetInput
    currency: Currency
    execution_offer_ids: tuple[Identifier, Identifier]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    execution_plan_id: UUID = Field(default_factory=uuid4)

    @model_validator(mode="after")
    def valid_request(self):
        if len(set(self.execution_offer_ids)) != 2:
            raise ValueError("execution_offer_ids must be distinct")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must include timezone information")
        return self


class BonusEngineEvaluation(DomainModel):
    calculation_result: QualifyingBetResult | FreeBetResult
    strategy_result: StrategyResult
    worst_case_profit_loss: Decimal
    is_profitable: bool
    execution_plan: ExecutionPlan

    @model_validator(mode="after")
    def approval_only_plan(self):
        if not self.execution_plan.requires_approval:
            raise ValueError("execution plans must require approval")
        if any(
            step.status is not ExecutionStatus.REQUIRES_APPROVAL
            for step in self.execution_plan.steps
        ):
            raise ValueError("execution steps must require approval")
        if self.execution_plan.strategy_result != self.strategy_result:
            raise ValueError("execution plan must use the evaluation strategy result")
        return self


class BonusEngine(StrategyEngine[BonusEngineRequest, BonusEngineEvaluation]):
    def evaluate(self, request):
        result = (
            calculate_qualifying_bet(request.inputs)
            if isinstance(request.inputs, QualifyingBetInput)
            else calculate_free_bet(request.inputs)
        )
        worst = min(result.back_win_profit_loss, result.lay_win_profit_loss)
        strategy = "qualifying_bet" if isinstance(result, QualifyingBetResult) else "free_bet"
        strategy_result = StrategyResult(
            strategy=strategy,
            opportunity_id=request.opportunity_id,
            stake=result.back_stake,
            expected_profit=worst,
            currency=request.currency,
            generated_at=request.generated_at,
        )
        plan = ExecutionPlan(
            id=request.execution_plan_id,
            strategy_result=strategy_result,
            steps=tuple(
                ExecutionStep(
                    offer_id=offer,
                    stake=stake,
                    status=ExecutionStatus.REQUIRES_APPROVAL,
                )
                for offer, stake in zip(
                    request.execution_offer_ids,
                    (result.back_stake, result.lay_stake),
                    strict=True,
                )
            ),
            created_at=request.generated_at,
            requires_approval=True,
        )
        return BonusEngineEvaluation(
            calculation_result=result,
            strategy_result=strategy_result,
            worst_case_profit_loss=worst,
            is_profitable=worst >= Decimal(0),
            execution_plan=plan,
        )
