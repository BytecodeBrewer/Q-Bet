from decimal import Decimal

from qbet.calculations import (
    ArbitrageOffer,
    FreeBetStakeReturn,
    SportsbookFreeBetInput,
    SportsbookQualifyingBetInput,
    calculate_sportsbook_free_bet,
    calculate_sportsbook_qualifying_bet,
)


def offer(outcome: str, odds: str) -> ArbitrageOffer:
    return ArbitrageOffer(
        outcome=outcome,
        odds=Decimal(odds),
        available_liquidity=Decimal("100"),
        stake_precision=Decimal("0.01"),
        currency="EUR",
    )


def test_fixed_odds_qualifying_bet_equalizes_two_sportsbook_outcomes() -> None:
    result = calculate_sportsbook_qualifying_bet(
        SportsbookQualifyingBetInput(
            promotion_offer=offer("home", "2.50"),
            hedge_offer=offer("away", "2.40"),
            qualifying_stake=Decimal("10.00"),
        )
    )

    assert result.hedge_stake == Decimal("10.42")
    assert result.promotion_outcome_profit_loss == Decimal("4.58")
    assert result.hedge_outcome_profit_loss == Decimal("4.588")
    assert result.guaranteed_profit_loss == Decimal("4.58")
    assert result.capital_required == Decimal("20.42")


def test_fixed_odds_snr_free_bet_reserves_only_cash_hedge() -> None:
    result = calculate_sportsbook_free_bet(
        SportsbookFreeBetInput(
            promotion_offer=offer("home", "3.00"),
            hedge_offer=offer("away", "2.50"),
            free_bet_amount=Decimal("10.00"),
            stake_return_rule=FreeBetStakeReturn.STAKE_NOT_RETURNED,
        )
    )

    assert result.hedge_stake == Decimal("8.00")
    assert result.promotion_outcome_profit_loss == Decimal("12.00")
    assert result.hedge_outcome_profit_loss == Decimal("12.000")
    assert result.guaranteed_profit_loss == Decimal("12.00")
    assert result.capital_required == Decimal("8.00")


def test_fixed_odds_stake_returned_free_bet_includes_returned_promo_stake() -> None:
    result = calculate_sportsbook_free_bet(
        SportsbookFreeBetInput(
            promotion_offer=offer("home", "3.00"),
            hedge_offer=offer("away", "2.50"),
            free_bet_amount=Decimal("10.00"),
            stake_return_rule=FreeBetStakeReturn.STAKE_RETURNED,
        )
    )

    assert result.hedge_stake == Decimal("12.00")
    assert result.guaranteed_profit_loss == Decimal("18.00")
    assert result.capital_required == Decimal("12.00")
