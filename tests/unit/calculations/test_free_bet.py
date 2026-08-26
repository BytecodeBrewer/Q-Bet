from decimal import Decimal

import pytest
from pydantic import ValidationError

from qbet.calculations.free_bet import (
    FreeBetInput,
    FreeBetStakeReturn,
    calculate_free_bet,
)


def test_calculates_stake_not_returned_free_bet_conversion() -> None:
    result = calculate_free_bet(
        FreeBetInput(
            free_bet_amount=Decimal("10"),
            back_odds=Decimal("3"),
            lay_odds=Decimal("3.2"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            stake_return_rule=FreeBetStakeReturn.STAKE_NOT_RETURNED,
        )
    )

    assert result.back_stake == Decimal("10")
    assert result.unrounded_lay_stake == Decimal("6.289308176100628930817610063")
    assert result.lay_stake == Decimal("6.29")
    assert result.rounding_impact == Decimal("-0.000691823899371069182389937")
    assert result.lay_liability == Decimal("13.838")
    assert result.back_win_profit_loss == Decimal("6.162")
    assert result.lay_win_profit_loss == Decimal("6.1642")
    assert result.expected_conversion_value == Decimal("6.162")


def test_calculates_stake_returned_free_bet_conversion() -> None:
    result = calculate_free_bet(
        FreeBetInput(
            free_bet_amount=Decimal("10"),
            back_odds=Decimal("3"),
            lay_odds=Decimal("3.2"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            stake_return_rule=FreeBetStakeReturn.STAKE_RETURNED,
        )
    )

    assert result.lay_stake == Decimal("9.43")
    assert result.lay_liability == Decimal("20.746")
    assert result.back_win_profit_loss == Decimal("9.254")
    assert result.lay_win_profit_loss == Decimal("9.2414")
    assert result.expected_conversion_value == Decimal("9.2414")


def test_commission_applies_only_to_exchange_winning_return() -> None:
    result = calculate_free_bet(
        FreeBetInput(
            free_bet_amount=Decimal("10"),
            back_odds=Decimal("3"),
            lay_odds=Decimal("3.2"),
            exchange_commission=Decimal("0.05"),
            stake_precision=Decimal("0.01"),
            stake_return_rule=FreeBetStakeReturn.STAKE_NOT_RETURNED,
        )
    )

    assert result.lay_win_profit_loss == result.lay_stake * Decimal("0.95")
    assert result.back_win_profit_loss == Decimal("20") - result.lay_liability


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("free_bet_amount", Decimal("-1")),
        ("back_odds", Decimal("1")),
        ("lay_odds", Decimal("1")),
        ("exchange_commission", Decimal("-0.01")),
        ("exchange_commission", Decimal("1")),
        ("stake_precision", Decimal("0")),
        ("stake_return_rule", "unknown"),
    ],
)
def test_rejects_invalid_free_bet_inputs(field: str, value: object) -> None:
    values: dict[str, object] = {
        "free_bet_amount": Decimal("10"),
        "back_odds": Decimal("3"),
        "lay_odds": Decimal("3.2"),
        "exchange_commission": Decimal("0.02"),
        "stake_precision": Decimal("0.01"),
        "stake_return_rule": FreeBetStakeReturn.STAKE_NOT_RETURNED,
    }
    values[field] = value

    with pytest.raises(ValidationError):
        FreeBetInput(**values)
