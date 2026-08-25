"""Deterministic two-outcome back/back arbitrage calculations."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from pydantic import Field, model_validator

from qbet.domain.models import Currency, DomainModel, Identifier, NonNegativeDecimal, PositiveDecimal


class ArbitrageOffer(DomainModel):
    """One bookmaker back offer participating in a two-way market."""

    outcome: Identifier
    odds: Decimal = Field(gt=Decimal("1"))
    available_liquidity: NonNegativeDecimal
    stake_precision: PositiveDecimal
    fee_rate: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), lt=Decimal("1"))
    currency: Currency

    @property
    def effective_odds(self) -> Decimal:
        return self.odds * (Decimal("1") - self.fee_rate)


class TwoWayArbitrageInput(DomainModel):
    """Inputs for allocating a fixed stake across two opposing back offers."""

    first_offer: ArbitrageOffer
    second_offer: ArbitrageOffer
    requested_total_stake: PositiveDecimal

    @model_validator(mode="after")
    def offers_are_opposing_and_share_currency(self) -> "TwoWayArbitrageInput":
        if self.first_offer.outcome == self.second_offer.outcome:
            raise ValueError("arbitrage offers must represent distinct outcomes")
        if self.first_offer.currency != self.second_offer.currency:
            raise ValueError("arbitrage offers must use the same currency")
        return self


class TwoWayArbitrageResult(DomainModel):
    """The rounded allocation and guaranteed result for a two-way market."""

    currency: Currency
    implied_probability: Decimal
    arbitrage_margin: Decimal
    first_stake: Decimal
    second_stake: Decimal
    first_unrounded_stake: Decimal
    second_unrounded_stake: Decimal
    total_stake: Decimal
    rounding_impact: Decimal
    first_outcome_return: Decimal
    second_outcome_return: Decimal
    guaranteed_profit_loss: Decimal
    is_profitable: bool


def calculate_two_way_arbitrage(inputs: TwoWayArbitrageInput) -> TwoWayArbitrageResult:
    """Allocate stakes and evaluate the actual precision-rounded market result."""

    first_effective_odds = inputs.first_offer.effective_odds
    second_effective_odds = inputs.second_offer.effective_odds
    effective_odds_sum = first_effective_odds + second_effective_odds

    first_unrounded_stake = (
        inputs.requested_total_stake * second_effective_odds / effective_odds_sum
    )
    second_unrounded_stake = (
        inputs.requested_total_stake * first_effective_odds / effective_odds_sum
    )
    first_stake = _round_down_to_increment(
        first_unrounded_stake,
        inputs.first_offer.stake_precision,
    )
    second_stake = _round_down_to_increment(
        second_unrounded_stake,
        inputs.second_offer.stake_precision,
    )
    if first_stake <= Decimal("0") or second_stake <= Decimal("0"):
        raise ValueError("stake precision rounds an arbitrage stake to zero")
    if first_stake > inputs.first_offer.available_liquidity:
        raise ValueError("first offer lacks available liquidity")
    if second_stake > inputs.second_offer.available_liquidity:
        raise ValueError("second offer lacks available liquidity")

    total_stake = first_stake + second_stake
    first_outcome_return = first_stake * first_effective_odds
    second_outcome_return = second_stake * second_effective_odds
    guaranteed_profit_loss = min(first_outcome_return, second_outcome_return) - total_stake
    implied_probability = (
        Decimal("1") / first_effective_odds
        + Decimal("1") / second_effective_odds
    )

    return TwoWayArbitrageResult(
        currency=inputs.first_offer.currency,
        implied_probability=implied_probability,
        arbitrage_margin=Decimal("1") - implied_probability,
        first_stake=first_stake,
        second_stake=second_stake,
        first_unrounded_stake=first_unrounded_stake,
        second_unrounded_stake=second_unrounded_stake,
        total_stake=total_stake,
        rounding_impact=inputs.requested_total_stake - total_stake,
        first_outcome_return=first_outcome_return,
        second_outcome_return=second_outcome_return,
        guaranteed_profit_loss=guaranteed_profit_loss,
        is_profitable=guaranteed_profit_loss >= Decimal("0"),
    )


def _round_down_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Return the largest supported stake increment not greater than value."""

    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment