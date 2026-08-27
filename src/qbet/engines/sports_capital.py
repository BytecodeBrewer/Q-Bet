"""Real-capital sports strategy evaluation."""
from qbet.calculations import DutchingInput, TwoWayArbitrageInput, calculate_dutching, calculate_two_way_arbitrage
from qbet.engines.base import BaseEngineRequest, BaseStrategy, CalculationResult

class SportsCapitalEngine:
    """Handles two-way arbitrage and dutching only."""
    def calculate(self, request: BaseEngineRequest) -> CalculationResult:
        if request.strategy is BaseStrategy.TWO_WAY_ARBITRAGE:
            if not isinstance(request.inputs, TwoWayArbitrageInput):
                raise ValueError("two_way_arbitrage requires TwoWayArbitrageInput")
            return calculate_two_way_arbitrage(request.inputs)
        if request.strategy is BaseStrategy.DUTCHING:
            if not isinstance(request.inputs, DutchingInput):
                raise ValueError("dutching requires DutchingInput")
            return calculate_dutching(request.inputs)
        raise ValueError("SportsCapitalEngine supports two_way_arbitrage and dutching only")