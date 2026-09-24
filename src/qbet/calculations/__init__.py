"""Pure matched-betting calculations."""

from .dutching import (
    DutchingAllocation,
    DutchingInput,
    DutchingOffer,
    DutchingResult,
    DutchingTargetMode,
    calculate_dutching,
)
from .free_bet import (
    FreeBetInput,
    FreeBetResult,
    FreeBetStakeReturn,
    calculate_free_bet,
)
from .fixed_odds_bonus import (
    GERMAN_BETTING_TAX_RATE,
    SportsbookFreeBetInput,
    SportsbookFreeBetResult,
    SportsbookQualifyingBetInput,
    SportsbookQualifyingBetResult,
    SportsbookTaxMode,
    SportsbookTaxTreatment,
    calculate_sportsbook_free_bet,
    calculate_sportsbook_qualifying_bet,
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
    "DutchingAllocation",
    "DutchingInput",
    "DutchingOffer",
    "DutchingResult",
    "DutchingTargetMode",
    "FreeBetInput",
    "GERMAN_BETTING_TAX_RATE",
    "FreeBetResult",
    "FreeBetStakeReturn",
    "QualifyingBetInput",
    "QualifyingBetResult",
    "SportsbookFreeBetInput",
    "SportsbookFreeBetResult",
    "SportsbookQualifyingBetInput",
    "SportsbookQualifyingBetResult",
    "SportsbookTaxMode",
    "SportsbookTaxTreatment",
    "TwoWayArbitrageInput",
    "TwoWayArbitrageResult",
    "calculate_dutching",
    "calculate_free_bet",
    "calculate_qualifying_bet",
    "calculate_sportsbook_free_bet",
    "calculate_sportsbook_qualifying_bet",
    "calculate_two_way_arbitrage",
]
