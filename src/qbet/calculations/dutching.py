"""Deterministic multi-outcome dutching calculations."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from qbet.calculations._rounding import RoundingPlan, choose_best_plan, stake_combinations
from qbet.domain.models import Currency, DomainModel, Identifier, NonNegativeDecimal, PositiveDecimal


class DutchingTargetMode(StrEnum):
    """The input quantity held fixed while building a dutching allocation."""

    TOTAL_STAKE = "total_stake"
    TARGET_RETURN = "target_return"


class DutchingOffer(DomainModel):
    """One back offer for a mutually exclusive dutching outcome."""

    outcome: Identifier
    odds: Decimal = Field(gt=Decimal("1"))
    available_liquidity: NonNegativeDecimal
    stake_precision: PositiveDecimal
    fee_rate: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), lt=Decimal("1"))
    currency: Currency

    @property
    def effective_odds(self) -> Decimal:
        return self.odds * (Decimal("1") - self.fee_rate)


class DutchingInput(DomainModel):
    """Inputs for an exhaustive two-to-four outcome dutching plan."""

    offers: tuple[DutchingOffer, ...] = Field(min_length=2, max_length=4)
    target_mode: DutchingTargetMode
    total_stake: PositiveDecimal | None = None
    target_return: PositiveDecimal | None = None
    outcomes_are_mutually_exclusive: Literal[True] = True
    outcomes_are_exhaustive: Literal[True]

    @model_validator(mode="after")
    def validates_target_and_offers(self) -> "DutchingInput":
        outcomes = tuple(offer.outcome for offer in self.offers)
        if len(set(outcomes)) != len(outcomes):
            raise ValueError("dutching offers must have distinct outcomes")
        if len({offer.currency for offer in self.offers}) != 1:
            raise ValueError("dutching offers must use the same currency")
        if self.target_mode == DutchingTargetMode.TOTAL_STAKE:
            if self.total_stake is None or self.target_return is not None:
                raise ValueError("total_stake mode requires only total_stake")
        elif self.target_return is None or self.total_stake is not None:
            raise ValueError("target_return mode requires only target_return")
        return self


class DutchingAllocation(DomainModel):
    """The actual rounded stake and return for one dutching outcome."""

    outcome: Identifier
    stake: Decimal
    unrounded_stake: Decimal
    outcome_return: Decimal


class DutchingResult(DomainModel):
    """The selected actual dutching allocation and its worst-case P/L."""

    currency: Currency
    target_mode: DutchingTargetMode
    requested_total_stake: Decimal | None
    requested_target_return: Decimal | None
    target_return: Decimal
    allocations: tuple[DutchingAllocation, ...]
    total_stake: Decimal
    rounding_impact: Decimal
    worst_case_profit_loss: Decimal
    is_profitable: bool


def calculate_dutching(inputs: DutchingInput) -> DutchingResult:
    """Calculate the best legal precision-rounded dutching allocation."""

    effective_odds = tuple(offer.effective_odds for offer in inputs.offers)
    inverse_odds_sum = sum((Decimal("1") / odds for odds in effective_odds), Decimal("0"))
    if inputs.target_mode == DutchingTargetMode.TOTAL_STAKE:
        requested_total_stake = inputs.total_stake
        assert requested_total_stake is not None
        target_return = requested_total_stake / inverse_odds_sum
    else:
        requested_total_stake = None
        target_return = inputs.target_return

    assert target_return is not None
    unrounded_stakes = tuple(target_return / odds for odds in effective_odds)
    plans: list[RoundingPlan] = []
    for stakes in stake_combinations(unrounded_stakes, tuple(offer.stake_precision for offer in inputs.offers)):
        total_stake = sum(stakes, Decimal("0"))
        outcome_returns = tuple(stake * odds for stake, odds in zip(stakes, effective_odds, strict=True))
        if any(stake <= Decimal("0") for stake in stakes):
            continue
        if any(stake > offer.available_liquidity for stake, offer in zip(stakes, inputs.offers, strict=True)):
            continue
        if requested_total_stake is not None and total_stake > requested_total_stake:
            continue
        if inputs.target_mode == DutchingTargetMode.TARGET_RETURN and min(outcome_returns) < target_return:
            continue
        plans.append(
            RoundingPlan(
                stakes=stakes,
                outcome_values=tuple(outcome_return - total_stake for outcome_return in outcome_returns),
            )
        )

    if not plans:
        raise ValueError("no rounded dutching plan fits target, stake, and liquidity limits")
    best_plan = choose_best_plan(plans, unrounded_stakes)
    total_stake = sum(best_plan.stakes, Decimal("0"))
    outcome_returns = tuple(
        stake * odds for stake, odds in zip(best_plan.stakes, effective_odds, strict=True)
    )
    if requested_total_stake is not None:
        rounding_impact = requested_total_stake - total_stake
    else:
        rounding_impact = min(outcome_returns) - target_return

    return DutchingResult(
        currency=inputs.offers[0].currency,
        target_mode=inputs.target_mode,
        requested_total_stake=inputs.total_stake,
        requested_target_return=inputs.target_return,
        target_return=target_return,
        allocations=tuple(
            DutchingAllocation(
                outcome=offer.outcome,
                stake=stake,
                unrounded_stake=unrounded_stake,
                outcome_return=outcome_return,
            )
            for offer, stake, unrounded_stake, outcome_return in zip(
                inputs.offers,
                best_plan.stakes,
                unrounded_stakes,
                outcome_returns,
                strict=True,
            )
        ),
        total_stake=total_stake,
        rounding_impact=rounding_impact,
        worst_case_profit_loss=best_plan.worst_case_value,
        is_profitable=best_plan.worst_case_value >= Decimal("0"),
    )
