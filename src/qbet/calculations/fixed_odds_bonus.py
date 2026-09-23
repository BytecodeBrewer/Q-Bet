"""Fixed-odds sportsbook hedging for current BonusEngine promotion types."""

from __future__ import annotations

from decimal import Decimal

from pydantic import model_validator

from qbet.calculations._rounding import RoundingPlan, choose_best_plan, surrounding_stake_candidates
from qbet.calculations.free_bet import FreeBetStakeReturn
from qbet.calculations.two_way_arbitrage import ArbitrageOffer
from qbet.domain.models import Currency, DomainModel, PositiveDecimal


class SportsbookQualifyingBetInput(DomainModel):
    """One qualifying sportsbook bet hedged by an opposing sportsbook back bet."""

    promotion_offer: ArbitrageOffer
    hedge_offer: ArbitrageOffer
    qualifying_stake: PositiveDecimal

    @model_validator(mode="after")
    def validate_market(self) -> "SportsbookQualifyingBetInput":
        _validate_pair(self.promotion_offer, self.hedge_offer)
        if self.qualifying_stake > self.promotion_offer.available_liquidity:
            raise ValueError("qualifying stake exceeds promotion sportsbook liquidity")
        if self.qualifying_stake % self.promotion_offer.stake_precision != 0:
            raise ValueError("qualifying stake must respect sportsbook stake precision")
        return self


class SportsbookQualifyingBetResult(DomainModel):
    currency: Currency
    promotion_stake: Decimal
    hedge_stake: Decimal
    unrounded_hedge_stake: Decimal
    rounding_impact: Decimal
    promotion_outcome_profit_loss: Decimal
    hedge_outcome_profit_loss: Decimal
    guaranteed_profit_loss: Decimal
    capital_required: Decimal


class SportsbookFreeBetInput(DomainModel):
    """One sportsbook free bet converted with an opposing sportsbook back bet."""

    promotion_offer: ArbitrageOffer
    hedge_offer: ArbitrageOffer
    free_bet_amount: PositiveDecimal
    stake_return_rule: FreeBetStakeReturn

    @model_validator(mode="after")
    def validate_market(self) -> "SportsbookFreeBetInput":
        _validate_pair(self.promotion_offer, self.hedge_offer)
        if self.free_bet_amount > self.promotion_offer.available_liquidity:
            raise ValueError("free-bet amount exceeds promotion sportsbook liquidity")
        return self


class SportsbookFreeBetResult(DomainModel):
    currency: Currency
    promotion_amount: Decimal
    hedge_stake: Decimal
    unrounded_hedge_stake: Decimal
    rounding_impact: Decimal
    promotion_outcome_profit_loss: Decimal
    hedge_outcome_profit_loss: Decimal
    guaranteed_profit_loss: Decimal
    capital_required: Decimal


def calculate_sportsbook_qualifying_bet(
    inputs: SportsbookQualifyingBetInput,
) -> SportsbookQualifyingBetResult:
    """Equalize a fixed qualifying stake across the two market outcomes."""

    unrounded = (
        inputs.qualifying_stake
        * inputs.promotion_offer.odds
        / inputs.hedge_offer.odds
    )
    plans: list[RoundingPlan] = []
    for hedge_stake in surrounding_stake_candidates(
        unrounded, inputs.hedge_offer.stake_precision
    ):
        if hedge_stake <= 0 or hedge_stake > inputs.hedge_offer.available_liquidity:
            continue
        plans.append(
            RoundingPlan(
                (hedge_stake,),
                (
                    inputs.qualifying_stake * (inputs.promotion_offer.odds - Decimal(1))
                    - hedge_stake,
                    hedge_stake * (inputs.hedge_offer.odds - Decimal(1))
                    - inputs.qualifying_stake,
                ),
            )
        )
    best = choose_best_plan(plans, (unrounded,))
    hedge_stake = best.stakes[0]
    promotion_pl, hedge_pl = best.outcome_values
    return SportsbookQualifyingBetResult(
        currency=inputs.promotion_offer.currency,
        promotion_stake=inputs.qualifying_stake,
        hedge_stake=hedge_stake,
        unrounded_hedge_stake=unrounded,
        rounding_impact=unrounded - hedge_stake,
        promotion_outcome_profit_loss=promotion_pl,
        hedge_outcome_profit_loss=hedge_pl,
        guaranteed_profit_loss=best.worst_case_value,
        capital_required=inputs.qualifying_stake + hedge_stake,
    )


def calculate_sportsbook_free_bet(
    inputs: SportsbookFreeBetInput,
) -> SportsbookFreeBetResult:
    """Convert a free bet using only fixed-odds sportsbook back offers."""

    promotion_return = (
        inputs.free_bet_amount * inputs.promotion_offer.odds
        if inputs.stake_return_rule is FreeBetStakeReturn.STAKE_RETURNED
        else inputs.free_bet_amount * (inputs.promotion_offer.odds - Decimal(1))
    )
    unrounded = promotion_return / inputs.hedge_offer.odds
    plans: list[RoundingPlan] = []
    for hedge_stake in surrounding_stake_candidates(
        unrounded, inputs.hedge_offer.stake_precision
    ):
        if hedge_stake <= 0 or hedge_stake > inputs.hedge_offer.available_liquidity:
            continue
        plans.append(
            RoundingPlan(
                (hedge_stake,),
                (
                    promotion_return - hedge_stake,
                    hedge_stake * (inputs.hedge_offer.odds - Decimal(1)),
                ),
            )
        )
    best = choose_best_plan(plans, (unrounded,))
    hedge_stake = best.stakes[0]
    promotion_pl, hedge_pl = best.outcome_values
    return SportsbookFreeBetResult(
        currency=inputs.promotion_offer.currency,
        promotion_amount=inputs.free_bet_amount,
        hedge_stake=hedge_stake,
        unrounded_hedge_stake=unrounded,
        rounding_impact=unrounded - hedge_stake,
        promotion_outcome_profit_loss=promotion_pl,
        hedge_outcome_profit_loss=hedge_pl,
        guaranteed_profit_loss=best.worst_case_value,
        capital_required=hedge_stake,
    )


def _validate_pair(first: ArbitrageOffer, second: ArbitrageOffer) -> None:
    if first.outcome == second.outcome:
        raise ValueError("bonus sportsbook hedge requires opposing outcomes")
    if first.currency != second.currency:
        raise ValueError("bonus sportsbook hedge requires one currency")
