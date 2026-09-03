"""Deterministic free-bet conversion calculations."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from qbet.calculations._rounding import (
    RoundingPlan,
    choose_best_plan,
    surrounding_stake_candidates,
)
from qbet.domain.models import DomainModel, PositiveDecimal


class FreeBetStakeReturn(StrEnum):
    """Whether the bookmaker returns the promotional stake on a back win."""

    STAKE_NOT_RETURNED = "stake_not_returned"
    STAKE_RETURNED = "stake_returned"


class FreeBetInput(DomainModel):
    """Inputs required to convert one free bet through an exchange lay bet."""

    free_bet_amount: PositiveDecimal
    back_odds: Decimal = Field(gt=Decimal(1))
    lay_odds: Decimal = Field(gt=Decimal(1))
    exchange_commission: Decimal = Field(ge=Decimal(0), lt=Decimal(1))
    stake_precision: PositiveDecimal
    stake_return_rule: FreeBetStakeReturn


class FreeBetResult(DomainModel):
    """A risk-aware precision-rounded free-bet conversion plan."""

    back_stake: Decimal
    lay_stake: Decimal
    unrounded_lay_stake: Decimal
    rounding_impact: Decimal
    lay_liability: Decimal
    back_win_profit_loss: Decimal
    lay_win_profit_loss: Decimal
    expected_conversion_value: Decimal


def calculate_free_bet(inputs: FreeBetInput) -> FreeBetResult:
    """Calculate free-bet conversion using the strongest permitted rounded hedge."""

    back_win_return = _back_win_return(inputs)
    unrounded_lay_stake = back_win_return / (inputs.lay_odds - inputs.exchange_commission)
    plans: list[tuple[RoundingPlan, Decimal]] = []
    for lay_stake in surrounding_stake_candidates(unrounded_lay_stake, inputs.stake_precision):
        if lay_stake <= Decimal(0):
            continue
        lay_liability = lay_stake * (inputs.lay_odds - Decimal(1))
        back_win_profit_loss = back_win_return - lay_liability
        lay_win_profit_loss = lay_stake * (Decimal(1) - inputs.exchange_commission)
        plans.append(
            (
                RoundingPlan((lay_stake,), (back_win_profit_loss, lay_win_profit_loss)),
                lay_liability,
            )
        )

    if not plans:
        raise ValueError("stake_precision rounds the lay stake to zero")
    best_plan = choose_best_plan((plan for plan, _ in plans), (unrounded_lay_stake,))
    lay_liability = next(liability for plan, liability in plans if plan == best_plan)
    back_win_profit_loss, lay_win_profit_loss = best_plan.outcome_values

    return FreeBetResult(
        back_stake=inputs.free_bet_amount,
        lay_stake=best_plan.stakes[0],
        unrounded_lay_stake=unrounded_lay_stake,
        rounding_impact=unrounded_lay_stake - best_plan.stakes[0],
        lay_liability=lay_liability,
        back_win_profit_loss=back_win_profit_loss,
        lay_win_profit_loss=lay_win_profit_loss,
        expected_conversion_value=best_plan.worst_case_value,
    )


def _back_win_return(inputs: FreeBetInput) -> Decimal:
    if inputs.stake_return_rule is FreeBetStakeReturn.STAKE_RETURNED:
        return inputs.free_bet_amount * inputs.back_odds
    return inputs.free_bet_amount * (inputs.back_odds - Decimal(1))
