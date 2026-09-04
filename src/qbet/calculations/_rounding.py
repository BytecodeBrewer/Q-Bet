"""Shared, risk-aware stake rounding primitives."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from itertools import product


@dataclass(frozen=True, slots=True)
class RoundingPlan:
    """One permitted rounded allocation and its resulting outcome values."""

    stakes: tuple[Decimal, ...]
    outcome_values: tuple[Decimal, ...]

    @property
    def worst_case_value(self) -> Decimal:
        return min(self.outcome_values)


def surrounding_stake_candidates(value: Decimal, increment: Decimal) -> tuple[Decimal, ...]:
    """Return the permitted stake increments immediately below and above value.

    A zero floor is preserved so callers can reject it while still considering
    the smallest positive permitted stake above the ideal value.
    """

    lower = (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment
    if lower == value:
        return (lower,)
    return (lower, lower + increment)


def stake_combinations(
    unrounded_stakes: tuple[Decimal, ...],
    increments: tuple[Decimal, ...],
) -> Iterable[tuple[Decimal, ...]]:
    """Yield floor/ceiling combinations for every stake in an allocation."""

    choices = [
        surrounding_stake_candidates(stake, increment)
        for stake, increment in zip(unrounded_stakes, increments, strict=True)
    ]
    return product(*choices)


def choose_best_plan(
    plans: Iterable[RoundingPlan],
    unrounded_stakes: tuple[Decimal, ...],
) -> RoundingPlan:
    """Choose the legal plan with the strongest worst-case outcome.

    Ties are resolved by distance to the mathematically ideal stakes, then by
    lower total stake. The latter keeps capital use conservative when outcome
    quality is otherwise identical.
    """

    candidates = tuple(plans)
    if not candidates:
        raise ValueError("no valid rounded stake plan is available")

    return max(
        candidates,
        key=lambda plan: (
            plan.worst_case_value,
            -sum(
                abs(stake - ideal)
                for stake, ideal in zip(plan.stakes, unrounded_stakes, strict=True)
            ),
            -sum(plan.stakes),
        ),
    )
