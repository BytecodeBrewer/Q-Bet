from decimal import Decimal

from qbet.calculations import (
    ArbitrageOffer,
    FreeBetStakeReturn,
    SportsbookFreeBetInput,
    SportsbookQualifyingBetInput,
    SportsbookTaxMode,
    SportsbookTaxTreatment,
    calculate_sportsbook_free_bet,
    calculate_sportsbook_qualifying_bet,
)

NO_TAX = SportsbookTaxTreatment(mode=SportsbookTaxMode.NONE)


def offer(outcome: str, odds: str, *, fee_rate: str = "0") -> ArbitrageOffer:
    return ArbitrageOffer(
        outcome=outcome,
        odds=Decimal(odds),
        available_liquidity=Decimal("100"),
        stake_precision=Decimal("0.01"),
        fee_rate=Decimal(fee_rate),
        currency="EUR",
    )


def test_fixed_odds_qualifying_bet_equalizes_two_sportsbook_outcomes() -> None:
    result = calculate_sportsbook_qualifying_bet(
        SportsbookQualifyingBetInput(
            promotion_offer=offer("home", "2.50"),
            hedge_offer=offer("away", "2.40"),
            promotion_tax=NO_TAX,
            hedge_tax=NO_TAX,
            qualifying_stake=Decimal("10.00"),
        )
    )

    assert result.hedge_stake == Decimal("10.42")
    assert result.promotion_outcome_profit_loss == Decimal("4.58")
    assert result.hedge_outcome_profit_loss == Decimal("4.588")
    assert result.guaranteed_profit_loss == Decimal("4.58")
    assert result.upfront_tax_cost == Decimal("0")
    assert result.capital_required == Decimal("20.42")


def test_fixed_odds_snr_free_bet_reserves_only_cash_hedge() -> None:
    result = calculate_sportsbook_free_bet(
        SportsbookFreeBetInput(
            promotion_offer=offer("home", "3.00"),
            hedge_offer=offer("away", "2.50"),
            promotion_tax=NO_TAX,
            hedge_tax=NO_TAX,
            free_bet_amount=Decimal("10.00"),
            stake_return_rule=FreeBetStakeReturn.STAKE_NOT_RETURNED,
        )
    )

    assert result.hedge_stake == Decimal("8.00")
    assert result.promotion_outcome_profit_loss == Decimal("12.00")
    assert result.hedge_outcome_profit_loss == Decimal("12.000")
    assert result.guaranteed_profit_loss == Decimal("12.00")
    assert result.upfront_tax_cost == Decimal("0")
    assert result.capital_required == Decimal("8.00")


def test_fixed_odds_stake_returned_free_bet_includes_returned_promo_stake() -> None:
    result = calculate_sportsbook_free_bet(
        SportsbookFreeBetInput(
            promotion_offer=offer("home", "3.00"),
            hedge_offer=offer("away", "2.50"),
            promotion_tax=NO_TAX,
            hedge_tax=NO_TAX,
            free_bet_amount=Decimal("10.00"),
            stake_return_rule=FreeBetStakeReturn.STAKE_RETURNED,
        )
    )

    assert result.hedge_stake == Decimal("12.00")
    assert result.guaranteed_profit_loss == Decimal("18.00")
    assert result.capital_required == Decimal("12.00")


def test_fixed_odds_qualifying_bet_rounding_uses_explicit_fee_and_tax_terms() -> None:
    result = calculate_sportsbook_qualifying_bet(
        SportsbookQualifyingBetInput(
            promotion_offer=offer("home", "2.50", fee_rate="0.02"),
            hedge_offer=offer("away", "2.40", fee_rate="0.01"),
            promotion_tax=SportsbookTaxTreatment(mode=SportsbookTaxMode.STAKE),
            hedge_tax=SportsbookTaxTreatment(mode=SportsbookTaxMode.PROFIT),
            qualifying_stake=Decimal("10.00"),
        )
    )

    assert result.hedge_stake == Decimal("10.64")
    assert result.promotion_outcome_profit_loss == Decimal("3.330")
    assert result.hedge_outcome_profit_loss == Decimal("3.33468608")
    assert result.guaranteed_profit_loss == Decimal("3.330")
    assert result.upfront_tax_cost == Decimal("0.530")
    assert result.capital_required == Decimal("21.170")
    assert result.promotion_fee_rate == Decimal("0.02")
    assert result.hedge_fee_rate == Decimal("0.01")
    assert result.promotion_tax_mode is SportsbookTaxMode.STAKE
    assert result.hedge_tax_mode is SportsbookTaxMode.PROFIT
