"""Typed strategy dispatch for Q-Bet's matched-betting Base Engine."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import TypeAlias
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from qbet.calculations import (
    DutchingInput,
    DutchingResult,
    FreeBetInput,
    FreeBetResult,
    QualifyingBetInput,
    QualifyingBetResult,
    TwoWayArbitrageInput,
    TwoWayArbitrageResult,
    calculate_dutching,
    calculate_free_bet,
    calculate_qualifying_bet,
    calculate_two_way_arbitrage,
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


class BaseStrategy(StrEnum):
    """Strategies currently supported by the Base Engine."""

    QUALIFYING_BET = "qualifying_bet"
    FREE_BET = "free_bet"
    TWO_WAY_ARBITRAGE = "two_way_arbitrage"
    DUTCHING = "dutching"


CalculationInput: TypeAlias = (
    QualifyingBetInput | FreeBetInput | TwoWayArbitrageInput | DutchingInput
)
CalculationResult: TypeAlias = (
    QualifyingBetResult | FreeBetResult | TwoWayArbitrageResult | DutchingResult
)


class BaseEngineRequest(DomainModel):
    """One explicit strategy request evaluated by the Base Engine."""

    opportunity_id: Identifier
    strategy: BaseStrategy
    inputs: CalculationInput
    currency: Currency
    execution_offer_ids: tuple[Identifier, ...] = Field(min_length=2)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    execution_plan_id: UUID = Field(default_factory=uuid4)

    @model_validator(mode="after")
    def request_matches_strategy_and_execution_steps(self) -> "BaseEngineRequest":
        expected_input_type = {
            BaseStrategy.QUALIFYING_BET: QualifyingBetInput,
            BaseStrategy.FREE_BET: FreeBetInput,
            BaseStrategy.TWO_WAY_ARBITRAGE: TwoWayArbitrageInput,
            BaseStrategy.DUTCHING: DutchingInput,
        }[self.strategy]
        if not isinstance(self.inputs, expected_input_type):
            raise ValueError(f"{self.strategy} requires {expected_input_type.__name__}")
        if len(set(self.execution_offer_ids)) != len(self.execution_offer_ids):
            raise ValueError("execution_offer_ids must be distinct")
        expected_step_count = _expected_step_count(self.inputs)
        if len(self.execution_offer_ids) != expected_step_count:
            raise ValueError(f"{self.strategy} requires {expected_step_count} execution offer ids")
        input_currency = _input_currency(self.inputs)
        if input_currency is not None and input_currency != self.currency:
            raise ValueError("request currency must match calculation input currency")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must include timezone information")
        return self


class BaseEngineEvaluation(DomainModel):
    """One calculator result plus its approval-only execution plan."""

    strategy: BaseStrategy
    calculation_result: CalculationResult
    strategy_result: StrategyResult
    currency: Currency
    worst_case_profit_loss: Decimal
    is_profitable: bool
    execution_plan: ExecutionPlan

    @model_validator(mode="after")
    def execution_plan_stays_approval_only(self) -> "BaseEngineEvaluation":
        if not self.execution_plan.requires_approval:
            raise ValueError("base engine execution plans must require approval")
        if any(step.status is not ExecutionStatus.REQUIRES_APPROVAL for step in self.execution_plan.steps):
            raise ValueError("base engine execution steps must require approval")
        if self.execution_plan.strategy_result != self.strategy_result:
            raise ValueError("execution plan must use the evaluation strategy result")
        if self.strategy_result.currency != self.currency:
            raise ValueError("strategy result currency must match evaluation currency")
        return self


class BaseEngine:
    """Compatibility facade that delegates to isolated strategy engines."""

    def __init__(self, bonus_engine: StrategyEngine | None = None, capital_engine: StrategyEngine | None = None) -> None:
        from qbet.engines.bonus import BonusEngine
        from qbet.engines.sports_capital import SportsCapitalEngine
        self.bonus_engine = bonus_engine or BonusEngine()
        self.capital_engine = capital_engine or SportsCapitalEngine()

    def evaluate(self, request: BaseEngineRequest) -> BaseEngineEvaluation:
        """Evaluate one supported strategy and return an approval-only plan."""

        calculation_result = self._calculate(request)
        strategy_stake, worst_case_profit_loss, is_profitable, stake_values = _result_summary(calculation_result)
        strategy_result = StrategyResult(
            strategy=request.strategy,
            opportunity_id=request.opportunity_id,
            stake=strategy_stake,
            expected_profit=worst_case_profit_loss,
            currency=request.currency,
            generated_at=request.generated_at,
        )
        execution_plan = ExecutionPlan(
            id=request.execution_plan_id,
            strategy_result=strategy_result,
            steps=tuple(
                ExecutionStep(
                    offer_id=offer_id,
                    stake=stake,
                    status=ExecutionStatus.REQUIRES_APPROVAL,
                )
                for offer_id, stake in zip(request.execution_offer_ids, stake_values, strict=True)
            ),
            created_at=request.generated_at,
            requires_approval=True,
            notes=("Generated by Base Engine; user approval is required before execution.",),
        )
        return BaseEngineEvaluation(
            strategy=request.strategy,
            calculation_result=calculation_result,
            strategy_result=strategy_result,
            currency=request.currency,
            worst_case_profit_loss=worst_case_profit_loss,
            is_profitable=is_profitable,
            execution_plan=execution_plan,
        )

    @staticmethod
    def _calculate(request: BaseEngineRequest) -> CalculationResult:
        match request.inputs:
            case QualifyingBetInput():
                return calculate_qualifying_bet(request.inputs)
            case FreeBetInput():
                return calculate_free_bet(request.inputs)
            case TwoWayArbitrageInput():
                return calculate_two_way_arbitrage(request.inputs)
            case DutchingInput():
                return calculate_dutching(request.inputs)
        raise TypeError("unsupported Base Engine calculation input")


def _expected_step_count(inputs: CalculationInput) -> int:
    if isinstance(inputs, DutchingInput):
        return len(inputs.offers)
    return 2


def _input_currency(inputs: CalculationInput) -> Currency | None:
    if isinstance(inputs, TwoWayArbitrageInput):
        return inputs.first_offer.currency
    if isinstance(inputs, DutchingInput):
        return inputs.offers[0].currency
    return None


def _result_summary(
    result: CalculationResult,
) -> tuple[Decimal, Decimal, bool, tuple[Decimal, ...]]:
    if isinstance(result, QualifyingBetResult):
        worst_case = min(result.back_win_profit_loss, result.lay_win_profit_loss)
        return result.back_stake, worst_case, worst_case >= Decimal("0"), (
            result.back_stake,
            result.lay_stake,
        )
    if isinstance(result, FreeBetResult):
        worst_case = min(result.back_win_profit_loss, result.lay_win_profit_loss)
        return result.back_stake, worst_case, worst_case >= Decimal("0"), (
            result.back_stake,
            result.lay_stake,
        )
    if isinstance(result, TwoWayArbitrageResult):
        stakes = (result.first_stake, result.second_stake)
        return sum(stakes, Decimal("0")), result.guaranteed_profit_loss, result.is_profitable, stakes
    stakes = tuple(allocation.stake for allocation in result.allocations)
    return sum(stakes, Decimal("0")), result.worst_case_profit_loss, result.is_profitable, stakes