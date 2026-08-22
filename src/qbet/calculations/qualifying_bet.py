"""Deterministic qualifying-bet calculations."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from pydantic import Field

from qbet.domain.models import DomainModel, NonNegativeDecimal, PositiveDecimal


class QualifyingBetInput(DomainModel):
    """Inputs required to hedge a bookmaker back bet on an exchange."""

    back_odds: Decimal = Field(gt=Decimal("1"))
    lay_odds: Decimal = Field(gt=Decimal("1"))
    back_stake: PositiveDecimal
    exchange_commission: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))
    stake_precision: PositiveDecimal
    max_lay_liability: NonNegativeDecimal


class QualifyingBetResult(DomainModel):
    """A rounded hedge plan and the resulting profit or loss for either outcome."""

    back_stake: Decimal
    lay_stake: Decimal
    unrounded_lay_stake: Decimal
    rounding_impact: Decimal
    lay_liability: Decimal
    back_win_profit_loss: Decimal
    lay_win_profit_loss: Decimal
    expected_qualifying_cost: Decimal


def calculate_qualifying_bet(inputs: QualifyingBetInput) -> QualifyingBetResult:
    """Calculate a conservative, precision-rounded qualifying-bet hedge.

    The expected qualifying cost is the loss of the less favourable outcome. It
    remains negative when both outcomes are profitable.
    """

    unrounded_lay_stake = (
        inputs.back_stake * inputs.back_odds
        / (inputs.lay_odds - inputs.exchange_commission)
    )
    lay_stake = _round_down_to_increment(
        unrounded_lay_stake,
        inputs.stake_precision,
    )
    if lay_stake <= Decimal("0"):
        raise ValueError("stake_precision rounds the lay stake to zero")

    lay_liability = lay_stake * (inputs.lay_odds - Decimal("1"))
    if lay_liability > inputs.max_lay_liability:
        raise ValueError("lay liability exceeds max_lay_liability")

    back_win_profit_loss = (
        inputs.back_stake * (inputs.back_odds - Decimal("1"))
        - lay_liability
    )
    lay_win_profit_loss = (
        lay_stake * (Decimal("1") - inputs.exchange_commission)
        - inputs.back_stake
    )

    return QualifyingBetResult(
        back_stake=inputs.back_stake,
        lay_stake=lay_stake,
        unrounded_lay_stake=unrounded_lay_stake,
        rounding_impact=unrounded_lay_stake - lay_stake,
        lay_liability=lay_liability,
        back_win_profit_loss=back_win_profit_loss,
        lay_win_profit_loss=lay_win_profit_loss,
        expected_qualifying_cost=-min(back_win_profit_loss, lay_win_profit_loss),
    )


def _round_down_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Return the largest supported stake increment not greater than value."""

    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment