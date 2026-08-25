"""Pure matched-betting calculations."""

from .free_bet import (
    FreeBetInput,
    FreeBetResult,
    FreeBetStakeReturn,
    calculate_free_bet,
)
from .qualifying_bet import (
    QualifyingBetInput,
    QualifyingBetResult,
    calculate_qualifying_bet,
)

__all__ = [
    "FreeBetInput",
    "FreeBetResult",
    "FreeBetStakeReturn",
    "calculate_free_bet",
    "QualifyingBetInput",
    "QualifyingBetResult",
    "calculate_qualifying_bet",
]