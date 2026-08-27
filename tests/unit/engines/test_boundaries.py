from decimal import Decimal

import pytest

from qbet.calculations import QualifyingBetInput, TwoWayArbitrageInput, ArbitrageOffer
from qbet.engines import BaseEngineRequest, BaseStrategy, BonusEngine, SportsCapitalEngine


def qualifying_request() -> BaseEngineRequest:
    return BaseEngineRequest(opportunity_id="opportunity", strategy=BaseStrategy.QUALIFYING_BET, inputs=QualifyingBetInput(back_odds=Decimal("2.5"), lay_odds=Decimal("2.6"), back_stake=Decimal("10"), exchange_commission=Decimal("0.02"), stake_precision=Decimal("0.01"), max_lay_liability=Decimal("100")), currency="EUR", execution_offer_ids=("book", "exchange"))


def arbitrage_request() -> BaseEngineRequest:
    offer = lambda outcome: ArbitrageOffer(outcome=outcome, odds=Decimal("2.2"), available_liquidity=Decimal("100"), stake_precision=Decimal("0.01"), currency="EUR")
    return BaseEngineRequest(opportunity_id="opportunity", strategy=BaseStrategy.TWO_WAY_ARBITRAGE, inputs=TwoWayArbitrageInput(first_offer=offer("home"), second_offer=offer("away"), requested_total_stake=Decimal("100")), currency="EUR", execution_offer_ids=("home", "away"))


def test_sub_engines_handle_their_own_strategy_groups() -> None:
    assert BonusEngine().calculate(qualifying_request()).back_stake == Decimal("10")
    assert SportsCapitalEngine().calculate(arbitrage_request()).total_stake == Decimal("100")


def test_sub_engines_reject_the_other_group() -> None:
    with pytest.raises(ValueError, match="BonusEngine"):
        BonusEngine().calculate(arbitrage_request())
    with pytest.raises(ValueError, match="SportsCapitalEngine"):
        SportsCapitalEngine().calculate(qualifying_request())