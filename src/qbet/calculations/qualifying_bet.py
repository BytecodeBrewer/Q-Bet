"""Deterministic qualifying-bet calculations."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from qbet.calculations._rounding import RoundingPlan, choose_best_plan, surrounding_stake_candidates
from qbet.domain.models import DomainModel, NonNegativeDecimal, PositiveDecimal


class QualifyingBetInput(DomainModel):
    """Inputs required to hedge a bookmaker back bet on an exchange."""

    back_odds: Decimal = Field(gt=Decimal("1"))
    lay_odds: Decimal = Field(gt=Decimal("1"))
    back_stake: PositiveDecimal
    exchange_commission: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))
    stake_precision: PositiveDecimal
    max_lay_liability: NonNegativeDecimal


class QualifyingBetResult(DomainModel):
    """A risk-aware precision-rounded hedge plan and its outcome values."""

    back_stake: Decimal
    lay_stake: Decimal
    unrounded_lay_stake: Decimal
    rounding_impact: Decimal
    lay_liability: Decimal
    back_win_profit_loss: Decimal
    lay_win_profit_loss: Decimal
    expected_qualifying_cost: Decimal


def calculate_qualifying_bet(inputs: QualifyingBetInput) -> QualifyingBetResult:
    """Calculate a qualifying bet using the strongest permitted rounded hedge."""

    unrounded_lay_stake = (
        inputs.back_stake * inputs.back_odds
        / (inputs.lay_odds - inputs.exchange_commission)
    )
    plans: list[tuple[RoundingPlan, Decimal]] = []
    for lay_stake in surrounding_stake_candidates(unrounded_lay_stake, inputs.stake_precision):
        if lay_stake <= Decimal("0"):
            continue
        lay_liability = lay_stake * (inputs.lay_odds - Decimal("1"))
        if lay_liability > inputs.max_lay_liability:
            continue
        back_win_profit_loss = inputs.back_stake * (inputs.back_odds - Decimal("1")) - lay_liability
        lay_win_profit_loss = lay_stake * (Decimal("1") - inputs.exchange_commission) - inputs.back_stake
        plans.append((RoundingPlan((lay_stake,), (back_win_profit_loss, lay_win_profit_loss)), lay_liability))

    if not plans:
        raise ValueError("no rounded lay stake fits max_lay_liability")
    best_plan = choose_best_plan((plan for plan, _ in plans), (unrounded_lay_stake,))
    lay_liability = next(liability for plan, liability in plans if plan == best_plan)
    back_win_profit_loss, lay_win_profit_loss = best_plan.outcome_values

    return QualifyingBetResult(
        back_stake=inputs.back_stake,
        lay_stake=best_plan.stakes[0],
        unrounded_lay_stake=unrounded_lay_stake,
        rounding_impact=unrounded_lay_stake - best_plan.stakes[0],
        lay_liability=lay_liability,
        back_win_profit_loss=back_win_profit_loss,
        lay_win_profit_loss=lay_win_profit_loss,
        expected_qualifying_cost=-best_plan.worst_case_value,
    )
