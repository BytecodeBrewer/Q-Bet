from decimal import Decimal

from qbet.calculations._rounding import (
    RoundingPlan,
    choose_best_plan,
    surrounding_stake_candidates,
)
from qbet.calculations.free_bet import (
    FreeBetInput,
    FreeBetStakeReturn,
    calculate_free_bet,
)


def test_selects_the_candidate_with_the_best_worst_case_outcome() -> None:
    plan = choose_best_plan(
        (
            RoundingPlan((Decimal("9.68"),), (Decimal("-0.488"), Decimal("-0.5136"))),
            RoundingPlan((Decimal("9.69"),), (Decimal("-0.504"), Decimal("-0.5038"))),
        ),
        (Decimal("9.689922480620155038759689922"),),
    )

    assert plan.stakes == (Decimal("9.69"),)


def test_whole_euro_increment_removes_fractional_stakes() -> None:
    result = calculate_free_bet(
        FreeBetInput(
            free_bet_amount=Decimal("10"),
            back_odds=Decimal("3"),
            lay_odds=Decimal("3.2"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("1"),
            stake_return_rule=FreeBetStakeReturn.STAKE_NOT_RETURNED,
        )
    )

    assert result.lay_stake == Decimal("6")


def test_precision_larger_than_the_ideal_stake_has_no_valid_candidate() -> None:
    assert surrounding_stake_candidates(Decimal("6.28"), Decimal("100")) == (Decimal("0"),)