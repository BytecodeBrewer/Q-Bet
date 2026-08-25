from decimal import Decimal

import pytest
from pydantic import ValidationError

from qbet.calculations.two_way_arbitrage import (
    ArbitrageOffer,
    TwoWayArbitrageInput,
    calculate_two_way_arbitrage,
)


def make_offer(**overrides: object) -> ArbitrageOffer:
    values: dict[str, object] = {
        "outcome": "home",
        "odds": Decimal("2.2"),
        "available_liquidity": Decimal("100"),
        "stake_precision": Decimal("0.01"),
        "fee_rate": Decimal("0"),
        "currency": "EUR",
    }
    values.update(overrides)
    return ArbitrageOffer(**values)


def test_calculates_a_profitable_two_way_arbitrage() -> None:
    result = calculate_two_way_arbitrage(
        TwoWayArbitrageInput(
            first_offer=make_offer(outcome="home"),
            second_offer=make_offer(outcome="away"),
            requested_total_stake=Decimal("100"),
        )
    )

    assert result.implied_probability == Decimal("0.9090909090909090909090909090")
    assert result.arbitrage_margin == Decimal("1") - result.implied_probability
    assert result.first_stake == Decimal("50")
    assert result.second_stake == Decimal("50")
    assert result.total_stake == Decimal("100")
    assert result.first_outcome_return == Decimal("110")
    assert result.second_outcome_return == Decimal("110")
    assert result.guaranteed_profit_loss == Decimal("10")
    assert result.is_profitable is True


@pytest.mark.parametrize(
    ("odds", "expected_profit", "is_profitable"),
    [
        (Decimal("2"), Decimal("0"), True),
        (Decimal("1.9"), Decimal("-5"), False),
    ],
)
def test_handles_break_even_and_non_profitable_markets(
    odds: Decimal,
    expected_profit: Decimal,
    is_profitable: bool,
) -> None:
    result = calculate_two_way_arbitrage(
        TwoWayArbitrageInput(
            first_offer=make_offer(outcome="home", odds=odds),
            second_offer=make_offer(outcome="away", odds=odds),
            requested_total_stake=Decimal("100"),
        )
    )

    assert result.guaranteed_profit_loss == expected_profit
    assert result.is_profitable is is_profitable


def test_applies_fees_and_reports_rounding_impact() -> None:
    result = calculate_two_way_arbitrage(
        TwoWayArbitrageInput(
            first_offer=make_offer(outcome="home", odds=Decimal("2.5"), stake_precision=Decimal("0.05")),
            second_offer=make_offer(outcome="away", odds=Decimal("2.2"), fee_rate=Decimal("0.05"), stake_precision=Decimal("0.05")),
            requested_total_stake=Decimal("10"),
        )
    )

    assert result.first_stake == Decimal("4.55")
    assert result.second_stake == Decimal("5.4")
    assert result.rounding_impact == Decimal("0.05")
    assert result.second_outcome_return == Decimal("11.2860")
    assert result.is_profitable is True


def test_rejects_a_plan_that_exceeds_available_liquidity() -> None:
    inputs = TwoWayArbitrageInput(
        first_offer=make_offer(outcome="home", available_liquidity=Decimal("40")),
        second_offer=make_offer(outcome="away"),
        requested_total_stake=Decimal("100"),
    )

    with pytest.raises(ValueError, match="first offer lacks available liquidity"):
        calculate_two_way_arbitrage(inputs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("odds", Decimal("1")),
        ("available_liquidity", Decimal("-1")),
        ("stake_precision", Decimal("0")),
        ("fee_rate", Decimal("-0.01")),
        ("fee_rate", Decimal("1")),
    ],
)
def test_rejects_invalid_offer_inputs(field: str, value: Decimal) -> None:
    values = {"outcome": "home"}
    values[field] = value

    with pytest.raises(ValidationError):
        make_offer(**values)


def test_rejects_duplicate_outcomes_and_currency_mismatch() -> None:
    with pytest.raises(ValidationError, match="distinct outcomes"):
        TwoWayArbitrageInput(
            first_offer=make_offer(outcome="home"),
            second_offer=make_offer(outcome="home"),
            requested_total_stake=Decimal("10"),
        )

    with pytest.raises(ValidationError, match="same currency"):
        TwoWayArbitrageInput(
            first_offer=make_offer(outcome="home", currency="EUR"),
            second_offer=make_offer(outcome="away", currency="GBP"),
            requested_total_stake=Decimal("10"),
        )