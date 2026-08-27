"""Promotional matched-betting strategy evaluation."""
from qbet.calculations import FreeBetInput, QualifyingBetInput, calculate_free_bet, calculate_qualifying_bet
from qbet.engines.base import BaseEngineRequest, BaseStrategy, CalculationResult

class BonusEngine:
    """Handles qualifying bets and free-bet conversions only."""
    def calculate(self, request: BaseEngineRequest) -> CalculationResult:
        if request.strategy is BaseStrategy.QUALIFYING_BET:
            if not isinstance(request.inputs, QualifyingBetInput):
                raise ValueError("qualifying_bet requires QualifyingBetInput")
            return calculate_qualifying_bet(request.inputs)
        if request.strategy is BaseStrategy.FREE_BET:
            if not isinstance(request.inputs, FreeBetInput):
                raise ValueError("free_bet requires FreeBetInput")
            return calculate_free_bet(request.inputs)
        raise ValueError("BonusEngine supports qualifying_bet and free_bet only")