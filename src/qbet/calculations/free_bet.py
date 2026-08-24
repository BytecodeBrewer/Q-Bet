"""Deterministic free-bet conversion calculations."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from enum import StrEnum

from pydantic import Field

from qbet.domain.models import DomainModel, PositiveDecimal


class FreeBetStakeReturn(StrEnum):
    """Whether the bookmaker returns the promotional stake on a back win."""

    STAKE_NOT_RETURNED = "stake_not_returned"
    STAKE_RETURNED = "stake_returned"


class FreeBetInput(DomainModel):
    """Inputs required to convert one free bet through an exchange lay bet."""

    free_bet_amount: PositiveDecimal
    back_odds: Decimal = Field(gt=Decimal("1"))
    lay_odds: Decimal = Field(gt=Decimal("1"))
    exchange_commission: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))
    stake_precision: PositiveDecimal
    stake_return_rule: FreeBetStakeReturn


class FreeBetResult(DomainModel):
    """A precision-rounded free-bet conversion plan and both outcome values."""

    back_stake: Decimal
    lay_stake: Decimal
    unrounded_lay_stake: Decimal
    rounding_impact: Decimal
    lay_liability: Decimal
    back_win_profit_loss: Decimal
    lay_win_profit_loss: Decimal
    expected_conversion_value: Decimal


def calculate_free_bet(inputs: FreeBetInput) -> FreeBetResult:
    """Calculate a conservative, precision-rounded free-bet conversion.

    The expected conversion value is the less favourable rounded outcome. It
    remains negative when a supplied scenario loses money in both outcomes.
    """

    back_win_return = _back_win_return(inputs)
    unrounded_lay_stake = back_win_return / (
        inputs.lay_odds - inputs.exchange_commission
    )
    lay_stake = _round_down_to_increment(
        unrounded_lay_stake,
        inputs.stake_precision,
    )
    if lay_stake <= Decimal("0"):
        raise ValueError("stake_precision rounds the lay stake to zero")

    lay_liability = lay_stake * (inputs.lay_odds - Decimal("1"))
    back_win_profit_loss = back_win_return - lay_liability
    lay_win_profit_loss = lay_stake * (Decimal("1") - inputs.exchange_commission)

    return FreeBetResult(
        back_stake=inputs.free_bet_amount,
        lay_stake=lay_stake,
        unrounded_lay_stake=unrounded_lay_stake,
        rounding_impact=unrounded_lay_stake - lay_stake,
        lay_liability=lay_liability,
        back_win_profit_loss=back_win_profit_loss,
        lay_win_profit_loss=lay_win_profit_loss,
        expected_conversion_value=min(back_win_profit_loss, lay_win_profit_loss),
    )


def _back_win_return(inputs: FreeBetInput) -> Decimal:
    if inputs.stake_return_rule is FreeBetStakeReturn.STAKE_RETURNED:
        return inputs.free_bet_amount * inputs.back_odds
    return inputs.free_bet_amount * (inputs.back_odds - Decimal("1"))


def _round_down_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Return the largest supported stake increment not greater than value."""

    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment