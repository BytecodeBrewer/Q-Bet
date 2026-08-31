"""Deterministic two-outcome back/back arbitrage calculations."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field, model_validator

from qbet.calculations._rounding import (
    RoundingPlan,
    choose_best_plan,
    stake_combinations,
)
from qbet.domain.models import (
    Currency,
    DomainModel,
    Identifier,
    NonNegativeDecimal,
    PositiveDecimal,
)


class ArbitrageOffer(DomainModel):
    """One bookmaker back offer participating in a two-way market."""

    outcome: Identifier
    odds: Decimal = Field(gt=Decimal(1))
    available_liquidity: NonNegativeDecimal
    stake_precision: PositiveDecimal
    fee_rate: Decimal = Field(default=Decimal(0), ge=Decimal(0), lt=Decimal(1))
    currency: Currency

    @property
    def effective_odds(self) -> Decimal:
        return self.odds * (Decimal(1) - self.fee_rate)


class TwoWayArbitrageInput(DomainModel):
    """Inputs for allocating a fixed stake across two opposing back offers."""

    first_offer: ArbitrageOffer
    second_offer: ArbitrageOffer
    requested_total_stake: PositiveDecimal

    @model_validator(mode="after")
    def offers_are_opposing_and_share_currency(self) -> TwoWayArbitrageInput:
        if self.first_offer.outcome == self.second_offer.outcome:
            raise ValueError("arbitrage offers must represent distinct outcomes")
        if self.first_offer.currency != self.second_offer.currency:
            raise ValueError("arbitrage offers must use the same currency")
        return self


class TwoWayArbitrageResult(DomainModel):
    """The risk-aware rounded allocation and guaranteed result for a market."""

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
    """Allocate stakes and choose the best legal precision-rounded plan."""

    first_effective_odds = inputs.first_offer.effective_odds
    second_effective_odds = inputs.second_offer.effective_odds
    effective_odds_sum = first_effective_odds + second_effective_odds
    first_unrounded_stake = (
        inputs.requested_total_stake * second_effective_odds / effective_odds_sum
    )
    second_unrounded_stake = (
        inputs.requested_total_stake * first_effective_odds / effective_odds_sum
    )
    unrounded_stakes = (first_unrounded_stake, second_unrounded_stake)

    plans: list[RoundingPlan] = []
    for first_stake, second_stake in stake_combinations(
        unrounded_stakes,
        (inputs.first_offer.stake_precision, inputs.second_offer.stake_precision),
    ):
        total_stake = first_stake + second_stake
        if first_stake <= Decimal(0) or second_stake <= Decimal(0):
            continue
        if total_stake > inputs.requested_total_stake:
            continue
        if first_stake > inputs.first_offer.available_liquidity:
            continue
        if second_stake > inputs.second_offer.available_liquidity:
            continue
        plans.append(
            RoundingPlan(
                (first_stake, second_stake),
                (
                    first_stake * first_effective_odds - total_stake,
                    second_stake * second_effective_odds - total_stake,
                ),
            )
        )

    if not plans:
        raise ValueError(
            "no rounded arbitrage plan fits total stake and liquidity limits"
        )
    best_plan = choose_best_plan(plans, unrounded_stakes)
    first_stake, second_stake = best_plan.stakes
    total_stake = first_stake + second_stake
    first_outcome_profit_loss, second_outcome_profit_loss = best_plan.outcome_values
    first_outcome_return = first_outcome_profit_loss + total_stake
    second_outcome_return = second_outcome_profit_loss + total_stake
    implied_probability = (
        Decimal(1) / first_effective_odds + Decimal(1) / second_effective_odds
    )

    return TwoWayArbitrageResult(
        currency=inputs.first_offer.currency,
        implied_probability=implied_probability,
        arbitrage_margin=Decimal(1) - implied_probability,
        first_stake=first_stake,
        second_stake=second_stake,
        first_unrounded_stake=first_unrounded_stake,
        second_unrounded_stake=second_unrounded_stake,
        total_stake=total_stake,
        rounding_impact=inputs.requested_total_stake - total_stake,
        first_outcome_return=first_outcome_return,
        second_outcome_return=second_outcome_return,
        guaranteed_profit_loss=best_plan.worst_case_value,
        is_profitable=best_plan.worst_case_value >= Decimal(0),
    )
