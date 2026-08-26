from decimal import Decimal

import pytest
from pydantic import ValidationError

from qbet.calculations.qualifying_bet import (
    QualifyingBetInput,
    calculate_qualifying_bet,
)


def test_calculates_both_outcomes_with_commission_and_precision() -> None:
    result = calculate_qualifying_bet(
        QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100"),
        )
    )

    assert result.unrounded_lay_stake == Decimal("9.689922480620155038759689922")
    assert result.lay_stake == Decimal("9.69")
    assert result.rounding_impact == Decimal("-0.000077519379844961240310078")
    assert result.lay_liability == Decimal("15.504")
    assert result.back_win_profit_loss == Decimal("-0.504")
    assert result.lay_win_profit_loss == Decimal("-0.5038")
    assert result.expected_qualifying_cost == Decimal("0.504")


def test_commission_is_applied_only_to_exchange_winning_return() -> None:
    result = calculate_qualifying_bet(
        QualifyingBetInput(
            back_odds=Decimal("2"),
            lay_odds=Decimal("2"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.05"),
            stake_precision=Decimal("0.0001"),
            max_lay_liability=Decimal("100"),
        )
    )

    assert result.lay_win_profit_loss == result.lay_stake * Decimal("0.95") - Decimal("10")
    assert result.back_win_profit_loss == Decimal("10") - result.lay_liability


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("back_odds", Decimal("1")),
        ("lay_odds", Decimal("1")),
        ("back_stake", Decimal("-1")),
        ("exchange_commission", Decimal("-0.01")),
        ("exchange_commission", Decimal("1")),
        ("stake_precision", Decimal("0")),
        ("max_lay_liability", Decimal("-1")),
    ],
)
def test_rejects_invalid_calculation_inputs(field: str, value: Decimal) -> None:
    values: dict[str, Decimal] = {
        "back_odds": Decimal("2.5"),
        "lay_odds": Decimal("2.6"),
        "back_stake": Decimal("10"),
        "exchange_commission": Decimal("0.02"),
        "stake_precision": Decimal("0.01"),
        "max_lay_liability": Decimal("100"),
    }
    values[field] = value

    with pytest.raises(ValidationError):
        QualifyingBetInput(**values)


def test_rejects_a_plan_that_exceeds_the_liability_limit() -> None:
    inputs = QualifyingBetInput(
        back_odds=Decimal("2.5"),
        lay_odds=Decimal("2.6"),
        back_stake=Decimal("10"),
        exchange_commission=Decimal("0.02"),
        stake_precision=Decimal("0.01"),
        max_lay_liability=Decimal("15"),
    )

    with pytest.raises(ValueError, match="fits max_lay_liability"):
        calculate_qualifying_bet(inputs)