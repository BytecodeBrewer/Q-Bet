"""Fixed-odds sportsbook hedging for current BonusEngine promotion types."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import model_validator

from qbet.calculations._rounding import RoundingPlan, choose_best_plan, surrounding_stake_candidates
from qbet.calculations.free_bet import FreeBetStakeReturn
from qbet.calculations.two_way_arbitrage import ArbitrageOffer
from qbet.domain.models import Currency, DomainModel, PositiveDecimal

GERMAN_BETTING_TAX_RATE = Decimal("0.053")


class SportsbookTaxMode(StrEnum):
    """How the provider/account applies German betting tax to one sportsbook leg."""

    STAKE = "stake"
    PROFIT = "profit"
    NONE = "none"


class SportsbookTaxTreatment(DomainModel):
    """Explicit tax treatment; the German statutory rate is fixed by the model."""

    mode: SportsbookTaxMode

    @property
    def rate(self) -> Decimal:
        return (
            Decimal(0)
            if self.mode is SportsbookTaxMode.NONE
            else GERMAN_BETTING_TAX_RATE
        )


class SportsbookQualifyingBetInput(DomainModel):
    """One qualifying sportsbook bet hedged by an opposing sportsbook back bet."""

    promotion_offer: ArbitrageOffer
    hedge_offer: ArbitrageOffer
    promotion_tax: SportsbookTaxTreatment
    hedge_tax: SportsbookTaxTreatment
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
    upfront_tax_cost: Decimal
    capital_required: Decimal
    promotion_fee_rate: Decimal
    hedge_fee_rate: Decimal
    promotion_tax_mode: SportsbookTaxMode
    hedge_tax_mode: SportsbookTaxMode


class SportsbookFreeBetInput(DomainModel):
    """One sportsbook free bet converted with an opposing sportsbook back bet."""

    promotion_offer: ArbitrageOffer
    hedge_offer: ArbitrageOffer
    promotion_tax: SportsbookTaxTreatment
    hedge_tax: SportsbookTaxTreatment
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
    upfront_tax_cost: Decimal
    capital_required: Decimal
    promotion_fee_rate: Decimal
    hedge_fee_rate: Decimal
    promotion_tax_mode: SportsbookTaxMode
    hedge_tax_mode: SportsbookTaxMode


def calculate_sportsbook_qualifying_bet(
    inputs: SportsbookQualifyingBetInput,
) -> SportsbookQualifyingBetResult:
    """Equalize a fixed qualifying stake across two taxed/fee-adjusted outcomes."""

    promotion_win = inputs.qualifying_stake * _cash_win_profit_per_unit(
        inputs.promotion_offer,
        inputs.promotion_tax,
    )
    promotion_loss = inputs.qualifying_stake * _cash_loss_per_unit(
        inputs.promotion_tax
    )
    hedge_win_unit = _cash_win_profit_per_unit(inputs.hedge_offer, inputs.hedge_tax)
    hedge_loss_unit = _cash_loss_per_unit(inputs.hedge_tax)
    unrounded = _balanced_hedge_stake(
        fixed_win=promotion_win,
        fixed_loss=promotion_loss,
        hedge_win_unit=hedge_win_unit,
        hedge_loss_unit=hedge_loss_unit,
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
                    promotion_win + hedge_stake * hedge_loss_unit,
                    promotion_loss + hedge_stake * hedge_win_unit,
                ),
            )
        )
    best = choose_best_plan(plans, (unrounded,))
    hedge_stake = best.stakes[0]
    promotion_pl, hedge_pl = best.outcome_values
    upfront_tax_cost = _stake_tax(
        inputs.qualifying_stake,
        inputs.promotion_tax,
    ) + _stake_tax(hedge_stake, inputs.hedge_tax)
    return SportsbookQualifyingBetResult(
        currency=inputs.promotion_offer.currency,
        promotion_stake=inputs.qualifying_stake,
        hedge_stake=hedge_stake,
        unrounded_hedge_stake=unrounded,
        rounding_impact=unrounded - hedge_stake,
        promotion_outcome_profit_loss=promotion_pl,
        hedge_outcome_profit_loss=hedge_pl,
        guaranteed_profit_loss=best.worst_case_value,
        upfront_tax_cost=upfront_tax_cost,
        capital_required=inputs.qualifying_stake + hedge_stake + upfront_tax_cost,
        promotion_fee_rate=inputs.promotion_offer.fee_rate,
        hedge_fee_rate=inputs.hedge_offer.fee_rate,
        promotion_tax_mode=inputs.promotion_tax.mode,
        hedge_tax_mode=inputs.hedge_tax.mode,
    )


def calculate_sportsbook_free_bet(
    inputs: SportsbookFreeBetInput,
) -> SportsbookFreeBetResult:
    """Convert a free bet using explicit fee/tax treatment on both sportsbook legs."""

    promotion_win = inputs.free_bet_amount * _free_bet_win_value_per_unit(
        inputs.promotion_offer,
        inputs.stake_return_rule,
        inputs.promotion_tax,
    )
    promotion_loss = inputs.free_bet_amount * _free_bet_loss_per_unit(
        inputs.promotion_tax
    )
    hedge_win_unit = _cash_win_profit_per_unit(inputs.hedge_offer, inputs.hedge_tax)
    hedge_loss_unit = _cash_loss_per_unit(inputs.hedge_tax)
    unrounded = _balanced_hedge_stake(
        fixed_win=promotion_win,
        fixed_loss=promotion_loss,
        hedge_win_unit=hedge_win_unit,
        hedge_loss_unit=hedge_loss_unit,
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
                    promotion_win + hedge_stake * hedge_loss_unit,
                    promotion_loss + hedge_stake * hedge_win_unit,
                ),
            )
        )
    best = choose_best_plan(plans, (unrounded,))
    hedge_stake = best.stakes[0]
    promotion_pl, hedge_pl = best.outcome_values
    upfront_tax_cost = _stake_tax(
        inputs.free_bet_amount,
        inputs.promotion_tax,
    ) + _stake_tax(hedge_stake, inputs.hedge_tax)
    return SportsbookFreeBetResult(
        currency=inputs.promotion_offer.currency,
        promotion_amount=inputs.free_bet_amount,
        hedge_stake=hedge_stake,
        unrounded_hedge_stake=unrounded,
        rounding_impact=unrounded - hedge_stake,
        promotion_outcome_profit_loss=promotion_pl,
        hedge_outcome_profit_loss=hedge_pl,
        guaranteed_profit_loss=best.worst_case_value,
        upfront_tax_cost=upfront_tax_cost,
        capital_required=hedge_stake + upfront_tax_cost,
        promotion_fee_rate=inputs.promotion_offer.fee_rate,
        hedge_fee_rate=inputs.hedge_offer.fee_rate,
        promotion_tax_mode=inputs.promotion_tax.mode,
        hedge_tax_mode=inputs.hedge_tax.mode,
    )


def _balanced_hedge_stake(
    *,
    fixed_win: Decimal,
    fixed_loss: Decimal,
    hedge_win_unit: Decimal,
    hedge_loss_unit: Decimal,
) -> Decimal:
    denominator = hedge_win_unit - hedge_loss_unit
    numerator = fixed_win - fixed_loss
    if denominator <= 0 or numerator <= 0:
        raise ValueError("no positive fixed-odds hedge solution exists")
    return numerator / denominator


def _cash_win_profit_per_unit(
    offer: ArbitrageOffer,
    tax: SportsbookTaxTreatment,
) -> Decimal:
    gross_profit = offer.effective_odds - Decimal(1)
    return (
        gross_profit
        - _profit_tax(gross_profit, tax)
        - _stake_tax(Decimal(1), tax)
    )


def _cash_loss_per_unit(tax: SportsbookTaxTreatment) -> Decimal:
    return -Decimal(1) - _stake_tax(Decimal(1), tax)


def _free_bet_win_value_per_unit(
    offer: ArbitrageOffer,
    stake_return_rule: FreeBetStakeReturn,
    tax: SportsbookTaxTreatment,
) -> Decimal:
    gross_profit = offer.effective_odds - Decimal(1)
    gross_value = (
        offer.effective_odds
        if stake_return_rule is FreeBetStakeReturn.STAKE_RETURNED
        else gross_profit
    )
    return (
        gross_value
        - _profit_tax(gross_profit, tax)
        - _stake_tax(Decimal(1), tax)
    )


def _free_bet_loss_per_unit(tax: SportsbookTaxTreatment) -> Decimal:
    return -_stake_tax(Decimal(1), tax)


def _stake_tax(stake: Decimal, tax: SportsbookTaxTreatment) -> Decimal:
    return (
        stake * tax.rate
        if tax.mode is SportsbookTaxMode.STAKE
        else Decimal(0)
    )


def _profit_tax(profit: Decimal, tax: SportsbookTaxTreatment) -> Decimal:
    return (
        max(profit, Decimal(0)) * tax.rate
        if tax.mode is SportsbookTaxMode.PROFIT
        else Decimal(0)
    )


def _validate_pair(first: ArbitrageOffer, second: ArbitrageOffer) -> None:
    if first.outcome == second.outcome:
        raise ValueError("bonus sportsbook hedge requires opposing outcomes")
    if first.currency != second.currency:
        raise ValueError("bonus sportsbook hedge requires one currency")
