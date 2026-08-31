from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from qbet.domain.models import (
    Event,
    ExecutionPlan,
    ExecutionStep,
    Market,
    Offer,
    OfferSide,
    Opportunity,
    StrategyResult,
)

TIMESTAMP = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def test_core_models_represent_a_matched_betting_plan() -> None:
    event = Event(
        id="event-1",
        sport="football",
        competition="Premier League",
        participants=("Team A", "Team B"),
        starts_at=TIMESTAMP,
    )
    market = Market(
        id="market-1",
        event_id=event.id,
        name="Match Odds",
        outcomes=("Team A", "Team B", "Draw"),
    )
    back_offer = Offer(
        id="offer-back",
        market_id=market.id,
        selection="Team A",
        provider="Bookmaker",
        side=OfferSide.BACK,
        odds=Decimal("2.4"),
        available_stake=Decimal(50),
        currency="EUR",
        observed_at=TIMESTAMP,
    )
    lay_offer = Offer(
        id="offer-lay",
        market_id=market.id,
        selection="Team A",
        provider="Exchange",
        side=OfferSide.LAY,
        odds=Decimal("2.5"),
        available_stake=Decimal(120),
        currency="EUR",
        observed_at=TIMESTAMP,
    )
    opportunity = Opportunity(
        id="opportunity-1",
        market_id=market.id,
        offer_ids=(back_offer.id, lay_offer.id),
        detected_at=TIMESTAMP,
        currency="EUR",
        expected_profit=Decimal("1.20"),
    )
    result = StrategyResult(
        strategy="qualifying_bet",
        opportunity_id=opportunity.id,
        stake=Decimal(10),
        expected_profit=Decimal("-0.20"),
        currency="EUR",
        generated_at=TIMESTAMP,
    )
    plan = ExecutionPlan(
        id=uuid4(),
        strategy_result=result,
        steps=(ExecutionStep(offer_id=back_offer.id, stake=Decimal(10)),),
        created_at=TIMESTAMP,
    )

    assert plan.strategy_result.opportunity_id == "opportunity-1"
    assert plan.requires_approval is True


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"odds": Decimal(1)}, "greater than 1"),
        ({"available_stake": Decimal("-0.01")}, "greater than or equal to 0"),
        ({"currency": "BTC"}, "Input should be"),
        ({"observed_at": datetime(2026, 8, 20, 12, 0)}, "timezone information"),
        ({"id": " "}, "at least 1 character"),
    ],
)
def test_offer_rejects_invalid_inputs(kwargs: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "id": "offer-1",
        "market_id": "market-1",
        "selection": "Team A",
        "provider": "Bookmaker",
        "side": OfferSide.BACK,
        "odds": Decimal("2.4"),
        "available_stake": Decimal(50),
        "currency": "EUR",
        "observed_at": TIMESTAMP,
    }
    values.update(kwargs)

    with pytest.raises(ValidationError, match=message):
        Offer.model_validate(values)


def test_opportunity_requires_two_offer_identifiers() -> None:
    with pytest.raises(ValidationError, match="at least 2 items"):
        Opportunity(
            id="opportunity-1",
            market_id="market-1",
            offer_ids=("offer-1",),
            detected_at=TIMESTAMP,
            currency="EUR",
            expected_profit=Decimal("1.20"),
        )


def test_opportunity_rejects_duplicate_normalized_offer_identifiers() -> None:
    with pytest.raises(ValidationError, match="distinct identifiers"):
        Opportunity(
            id="opportunity-1",
            market_id="market-1",
            offer_ids=("offer-1", " offer-1 "),
            detected_at=TIMESTAMP,
            currency="EUR",
            expected_profit=Decimal("1.20"),
        )


def test_execution_plan_requires_at_least_one_step() -> None:
    result = StrategyResult(
        strategy="arbitrage",
        opportunity_id="opportunity-1",
        stake=Decimal(10),
        expected_profit=Decimal(1),
        currency="EUR",
        generated_at=TIMESTAMP,
    )

    with pytest.raises(ValidationError, match="at least 1 item"):
        ExecutionPlan(
            id=uuid4(), strategy_result=result, steps=(), created_at=TIMESTAMP
        )
