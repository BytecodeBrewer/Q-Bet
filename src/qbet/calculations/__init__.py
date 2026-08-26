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
from .two_way_arbitrage import (
    ArbitrageOffer,
    TwoWayArbitrageInput,
    TwoWayArbitrageResult,
    calculate_two_way_arbitrage,
)

__all__ = [
    "ArbitrageOffer",
    "FreeBetInput",
    "FreeBetResult",
    "FreeBetStakeReturn",
    "QualifyingBetInput",
    "QualifyingBetResult",
    "TwoWayArbitrageInput",
    "TwoWayArbitrageResult",
    "calculate_free_bet",
    "calculate_qualifying_bet",
    "calculate_two_way_arbitrage",
]