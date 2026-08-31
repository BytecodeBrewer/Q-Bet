from decimal import Decimal

import pytest
from pydantic import ValidationError

from qbet.calculations.dutching import (
    DutchingInput,
    DutchingOffer,
    DutchingTargetMode,
    calculate_dutching,
)


def make_offer(**overrides: object) -> DutchingOffer:
    values: dict[str, object] = {
        "outcome": "home",
        "odds": Decimal(2),
        "available_liquidity": Decimal(100),
        "stake_precision": Decimal("0.01"),
        "fee_rate": Decimal(0),
        "currency": "EUR",
    }
    values.update(overrides)
    return DutchingOffer(**values)


def test_balances_three_outcomes_for_a_total_stake() -> None:
    result = calculate_dutching(
        DutchingInput(
            outcomes_are_exhaustive=True,
            offers=(
                make_offer(outcome="home", odds=Decimal(2)),
                make_offer(outcome="draw", odds=Decimal(3)),
                make_offer(outcome="away", odds=Decimal(6)),
            ),
            target_mode=DutchingTargetMode.TOTAL_STAKE,
            total_stake=Decimal(60),
        )
    )

    assert tuple(allocation.stake for allocation in result.allocations) == (
        Decimal(30),
        Decimal(20),
        Decimal(10),
    )
    assert tuple(allocation.outcome_return for allocation in result.allocations) == (
        Decimal(60),
        Decimal(60),
        Decimal(60),
    )
    assert result.total_stake == Decimal(60)
    assert result.worst_case_profit_loss == Decimal(0)
    assert result.is_profitable is True


def test_builds_a_target_return_plan_with_explicit_fees() -> None:
    result = calculate_dutching(
        DutchingInput(
            outcomes_are_exhaustive=True,
            offers=(
                make_offer(
                    outcome="home",
                    odds=Decimal("2.2"),
                    fee_rate=Decimal("0.09090909090909090909090909091"),
                ),
                make_offer(outcome="away", odds=Decimal(4), fee_rate=Decimal("0.1")),
            ),
            target_mode=DutchingTargetMode.TARGET_RETURN,
            target_return=Decimal(72),
        )
    )

    assert tuple(allocation.stake for allocation in result.allocations) == (
        Decimal(36),
        Decimal(20),
    )
    assert tuple(allocation.outcome_return for allocation in result.allocations) == (
        Decimal(72),
        Decimal("72.0"),
    )
    assert result.total_stake == Decimal(56)
    assert result.worst_case_profit_loss == Decimal(16)


def test_selects_risk_aware_whole_euro_rounding_under_total_stake_cap() -> None:
    result = calculate_dutching(
        DutchingInput(
            outcomes_are_exhaustive=True,
            offers=(
                make_offer(
                    outcome="one", odds=Decimal("2.5"), stake_precision=Decimal(1)
                ),
                make_offer(outcome="two", odds=Decimal(3), stake_precision=Decimal(1)),
                make_offer(
                    outcome="three", odds=Decimal(5), stake_precision=Decimal(1)
                ),
            ),
            target_mode=DutchingTargetMode.TOTAL_STAKE,
            total_stake=Decimal(100),
        )
    )

    assert tuple(allocation.stake for allocation in result.allocations) == (
        Decimal(42),
        Decimal(35),
        Decimal(21),
    )
    assert result.total_stake == Decimal(98)
    assert result.rounding_impact == Decimal(2)
    assert result.worst_case_profit_loss == Decimal(7)
    assert result.is_profitable is True


def test_reports_actual_rounded_loss_as_not_profitable() -> None:
    result = calculate_dutching(
        DutchingInput(
            outcomes_are_exhaustive=True,
            offers=(
                make_offer(outcome="home", odds=Decimal("1.5")),
                make_offer(outcome="away", odds=Decimal("1.5")),
            ),
            target_mode=DutchingTargetMode.TOTAL_STAKE,
            total_stake=Decimal(10),
        )
    )

    assert result.worst_case_profit_loss == Decimal("-2.5")
    assert result.is_profitable is False


def test_supports_four_mutually_exclusive_outcomes() -> None:
    result = calculate_dutching(
        DutchingInput(
            outcomes_are_exhaustive=True,
            offers=(
                make_offer(outcome="one", odds=Decimal(2)),
                make_offer(outcome="two", odds=Decimal(4)),
                make_offer(outcome="three", odds=Decimal(4)),
                make_offer(outcome="four", odds=Decimal(4)),
            ),
            target_mode=DutchingTargetMode.TARGET_RETURN,
            target_return=Decimal(80),
        )
    )

    assert tuple(allocation.stake for allocation in result.allocations) == (
        Decimal(40),
        Decimal(20),
        Decimal(20),
        Decimal(20),
    )
    assert result.total_stake == Decimal(100)


def test_rejects_plan_that_exceeds_available_liquidity() -> None:
    inputs = DutchingInput(
        outcomes_are_exhaustive=True,
        offers=(
            make_offer(outcome="home", available_liquidity=Decimal(20)),
            make_offer(outcome="away", odds=Decimal(3)),
        ),
        target_mode=DutchingTargetMode.TOTAL_STAKE,
        total_stake=Decimal(60),
    )

    with pytest.raises(ValueError, match="fits target, stake, and liquidity limits"):
        calculate_dutching(inputs)


@pytest.mark.parametrize(
    ("offers", "target_mode", "total_stake", "target_return", "exclusive"),
    [
        (
            (make_offer(outcome="home"),),
            DutchingTargetMode.TOTAL_STAKE,
            Decimal(10),
            None,
            True,
        ),
        (
            (make_offer(outcome="home"),) * 2,
            DutchingTargetMode.TOTAL_STAKE,
            Decimal(10),
            None,
            True,
        ),
        (
            (make_offer(outcome="home"), make_offer(outcome="away", currency="GBP")),
            DutchingTargetMode.TOTAL_STAKE,
            Decimal(10),
            None,
            True,
        ),
        (
            (make_offer(outcome="home"), make_offer(outcome="away")),
            DutchingTargetMode.TOTAL_STAKE,
            None,
            None,
            True,
        ),
        (
            (make_offer(outcome="home"), make_offer(outcome="away")),
            DutchingTargetMode.TARGET_RETURN,
            Decimal(10),
            Decimal(20),
            True,
        ),
        (
            (make_offer(outcome="home"), make_offer(outcome="away")),
            DutchingTargetMode.TOTAL_STAKE,
            Decimal(10),
            None,
            False,
        ),
    ],
)
def test_rejects_invalid_dutching_inputs(
    offers: tuple[DutchingOffer, ...],
    target_mode: DutchingTargetMode,
    total_stake: Decimal | None,
    target_return: Decimal | None,
    exclusive: bool,
) -> None:
    with pytest.raises(ValidationError):
        DutchingInput(
            outcomes_are_exhaustive=True,
            offers=offers,
            target_mode=target_mode,
            total_stake=total_stake,
            target_return=target_return,
            outcomes_are_mutually_exclusive=exclusive,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("odds", Decimal(1)),
        ("available_liquidity", Decimal(-1)),
        ("stake_precision", Decimal(0)),
        ("fee_rate", Decimal(1)),
    ],
)
def test_rejects_invalid_offer_inputs(field: str, value: Decimal) -> None:
    with pytest.raises(ValidationError):
        make_offer(**{field: value})


def test_requires_an_explicit_exhaustive_outcome_guarantee() -> None:
    with pytest.raises(ValidationError, match="outcomes_are_exhaustive"):
        DutchingInput(
            offers=(
                make_offer(outcome="home", odds=Decimal(3)),
                make_offer(outcome="away", odds=Decimal(3)),
            ),
            target_mode=DutchingTargetMode.TOTAL_STAKE,
            total_stake=Decimal(10),
        )
