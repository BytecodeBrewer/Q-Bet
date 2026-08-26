from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from qbet.calculations import (
    ArbitrageOffer,
    DutchingInput,
    DutchingOffer,
    DutchingTargetMode,
    FreeBetInput,
    FreeBetStakeReturn,
    QualifyingBetInput,
    TwoWayArbitrageInput,
)
from qbet.domain.models import ExecutionPlan
from qbet.engine import BaseEngine, BaseEngineEvaluation, BaseEngineRequest, BaseStrategy


GENERATED_AT = datetime(2026, 8, 26, tzinfo=timezone.utc)


def qualifying_inputs() -> QualifyingBetInput:
    return QualifyingBetInput(
        back_odds=Decimal("2.5"),
        lay_odds=Decimal("2.6"),
        back_stake=Decimal("10"),
        exchange_commission=Decimal("0.02"),
        stake_precision=Decimal("0.01"),
        max_lay_liability=Decimal("100"),
    )


def free_bet_inputs() -> FreeBetInput:
    return FreeBetInput(
        free_bet_amount=Decimal("10"),
        back_odds=Decimal("3"),
        lay_odds=Decimal("3.2"),
        exchange_commission=Decimal("0.02"),
        stake_precision=Decimal("0.01"),
        stake_return_rule=FreeBetStakeReturn.STAKE_NOT_RETURNED,
    )


def arbitrage_inputs(currency: str = "EUR") -> TwoWayArbitrageInput:
    return TwoWayArbitrageInput(
        first_offer=ArbitrageOffer(
            outcome="home",
            odds=Decimal("2.2"),
            available_liquidity=Decimal("100"),
            stake_precision=Decimal("0.01"),
            currency=currency,
        ),
        second_offer=ArbitrageOffer(
            outcome="away",
            odds=Decimal("2.2"),
            available_liquidity=Decimal("100"),
            stake_precision=Decimal("0.01"),
            currency=currency,
        ),
        requested_total_stake=Decimal("100"),
    )


def dutching_inputs() -> DutchingInput:
    return DutchingInput(
        outcomes_are_exhaustive=True,
        offers=(
            DutchingOffer(
                outcome="home",
                odds=Decimal("2"),
                available_liquidity=Decimal("100"),
                stake_precision=Decimal("0.01"),
                currency="EUR",
            ),
            DutchingOffer(
                outcome="away",
                odds=Decimal("2"),
                available_liquidity=Decimal("100"),
                stake_precision=Decimal("0.01"),
                currency="EUR",
            ),
        ),
        target_mode=DutchingTargetMode.TOTAL_STAKE,
        total_stake=Decimal("20"),
    )


@pytest.mark.parametrize(
    ("strategy", "inputs", "offer_ids", "expected_strategy_stake"),
    [
        (BaseStrategy.QUALIFYING_BET, qualifying_inputs(), ("bookmaker", "exchange"), Decimal("10")),
        (BaseStrategy.FREE_BET, free_bet_inputs(), ("bookmaker", "exchange"), Decimal("10")),
        (BaseStrategy.TWO_WAY_ARBITRAGE, arbitrage_inputs(), ("home-book", "away-book"), Decimal("100")),
        (BaseStrategy.DUTCHING, dutching_inputs(), ("home-book", "away-book"), Decimal("20")),
    ],
)
def test_evaluates_every_supported_strategy_through_one_entry_point(
    strategy: BaseStrategy,
    inputs: object,
    offer_ids: tuple[str, ...],
    expected_strategy_stake: Decimal,
) -> None:
    request = BaseEngineRequest(
        opportunity_id="opportunity-1",
        strategy=strategy,
        inputs=inputs,
        currency="EUR",
        execution_offer_ids=offer_ids,
        generated_at=GENERATED_AT,
        execution_plan_id=UUID("00000000-0000-0000-0000-000000000001"),
    )

    evaluation = BaseEngine().evaluate(request)

    assert evaluation.strategy is strategy
    assert evaluation.strategy_result.strategy == strategy
    assert evaluation.strategy_result.expected_profit == evaluation.worst_case_profit_loss
    assert evaluation.strategy_result.stake == expected_strategy_stake
    assert evaluation.execution_plan.requires_approval is True
    assert tuple(step.status.value for step in evaluation.execution_plan.steps) == (
        "requires_approval",
    ) * len(offer_ids)
    if strategy in {BaseStrategy.QUALIFYING_BET, BaseStrategy.FREE_BET}:
        expected_stakes = (
            evaluation.calculation_result.back_stake,
            evaluation.calculation_result.lay_stake,
        )
    elif strategy is BaseStrategy.TWO_WAY_ARBITRAGE:
        expected_stakes = (
            evaluation.calculation_result.first_stake,
            evaluation.calculation_result.second_stake,
        )
    else:
        expected_stakes = tuple(
            allocation.stake for allocation in evaluation.calculation_result.allocations
        )
    assert tuple(step.stake for step in evaluation.execution_plan.steps) == expected_stakes

def test_rejects_strategy_input_mismatch_before_evaluation() -> None:
    with pytest.raises(ValidationError, match="free_bet requires FreeBetInput"):
        BaseEngineRequest(
            opportunity_id="opportunity-1",
            strategy=BaseStrategy.FREE_BET,
            inputs=qualifying_inputs(),
            currency="EUR",
            execution_offer_ids=("bookmaker", "exchange"),
        )


def test_rejects_unsupported_strategy() -> None:
    with pytest.raises(ValidationError):
        BaseEngineRequest(
            opportunity_id="opportunity-1",
            strategy="unknown",
            inputs=qualifying_inputs(),
            currency="EUR",
            execution_offer_ids=("bookmaker", "exchange"),
        )


def test_rejects_mixed_currency_request_before_evaluation() -> None:
    with pytest.raises(ValidationError, match="request currency"):
        BaseEngineRequest(
            opportunity_id="opportunity-1",
            strategy=BaseStrategy.TWO_WAY_ARBITRAGE,
            inputs=arbitrage_inputs(),
            currency="GBP",
            execution_offer_ids=("home-book", "away-book"),
        )


def test_rejects_execution_plan_without_approval_requirement() -> None:
    evaluation = BaseEngine().evaluate(
        BaseEngineRequest(
            opportunity_id="opportunity-1",
            strategy=BaseStrategy.QUALIFYING_BET,
            inputs=qualifying_inputs(),
            currency="EUR",
            execution_offer_ids=("bookmaker", "exchange"),
            generated_at=GENERATED_AT,
        )
    )
    unsafe_plan = ExecutionPlan(
        id=evaluation.execution_plan.id,
        strategy_result=evaluation.strategy_result,
        steps=evaluation.execution_plan.steps,
        created_at=GENERATED_AT,
        requires_approval=False,
    )

    with pytest.raises(ValidationError, match="must require approval"):
        BaseEngineEvaluation(
            strategy=evaluation.strategy,
            calculation_result=evaluation.calculation_result,
            strategy_result=evaluation.strategy_result,
            currency=evaluation.currency,
            worst_case_profit_loss=evaluation.worst_case_profit_loss,
            is_profitable=evaluation.is_profitable,
            execution_plan=unsafe_plan,
        )