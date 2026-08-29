from decimal import Decimal

from qbet.calculations import ArbitrageOffer, QualifyingBetInput, TwoWayArbitrageInput
from qbet.domain.verification import ProviderState
from qbet.engines.bonus import BonusEngineRequest
from qbet.engines.sports_capital import SportsCapitalEngineRequest
from qbet.layers.verification import (
    COOLDOWN_ACTIVE_REASON,
    FREQUENCY_LIMIT_REASON,
    OperationalRiskLayer,
)


def bonus_request() -> BonusEngineRequest:
    return BonusEngineRequest(opportunity_id="bonus-opportunity", inputs=QualifyingBetInput(back_odds=Decimal("2.5"), lay_odds=Decimal("2.6"), back_stake=Decimal("10"), exchange_commission=Decimal("0.02"), stake_precision=Decimal("0.01"), max_lay_liability=Decimal("100")), currency="EUR", execution_offer_ids=("book", "exchange"))


def sports_request() -> SportsCapitalEngineRequest:
    offer = lambda outcome: ArbitrageOffer(outcome=outcome, odds=Decimal("2.2"), available_liquidity=Decimal("100"), stake_precision=Decimal("0.01"), currency="EUR")
    return SportsCapitalEngineRequest(opportunity_id="sports-opportunity", inputs=TwoWayArbitrageInput(first_offer=offer("home"), second_offer=offer("away"), requested_total_stake=Decimal("100")), currency="EUR", execution_offer_ids=("home", "away"))


def state(**changes: object) -> ProviderState:
    values = {"provider_id": "book", "active_bets_count": 0, "is_cooldown_active": False}
    values.update(changes)
    return ProviderState(**values)


def test_allows_both_engine_request_types_and_blocks_with_stable_reasons() -> None:
    layer = OperationalRiskLayer()
    assert layer.verify_opportunity(bonus_request(), state()).is_allowed is True
    assert layer.verify_opportunity(sports_request(), state()).is_allowed is True
    assert layer.verify_opportunity(bonus_request(), state(active_bets_count=2)).rejection_reason == FREQUENCY_LIMIT_REASON
    assert layer.verify_opportunity(sports_request(), state(is_cooldown_active=True)).rejection_reason == COOLDOWN_ACTIVE_REASON
    assert layer.verify_opportunity(bonus_request(), state(active_bets_count=2, is_cooldown_active=True)).rejection_reason == FREQUENCY_LIMIT_REASON