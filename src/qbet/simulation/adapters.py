"""Sandbox and concrete-engine adapters for the shared simulation contract."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from qbet.calculations import QualifyingBetInput, TwoWayArbitrageInput
from qbet.engines import (
    BonusEngine,
    BonusEngineEvaluation,
    BonusEngineRequest,
    SportsCapitalEngine,
    SportsCapitalEngineEvaluation,
    SportsCapitalEngineRequest,
)
from qbet.simulation.models import (
    SimulationEngine,
    SimulationEvaluation,
    SimulationRunConfig,
    SimulationStep,
)


class SimulationEngineAdapter(Protocol):
    """Supply deterministic simulation steps for one Q-Bet engine."""

    engine: SimulationEngine
    is_placeholder: bool

    def build_steps(self, config: SimulationRunConfig) -> tuple[SimulationStep, ...]:
        """Build engine-specific simulation steps without external actions."""
        ...


class BonusSimulationAdapter:
    """Evaluates BonusEngine requests as isolated simulation steps."""

    engine = SimulationEngine.BONUS
    is_placeholder = False

    def __init__(self, requests: tuple[BonusEngineRequest, ...]) -> None:
        self._requests = requests
        self._engine = BonusEngine()

    def build_steps(self, config: SimulationRunConfig) -> tuple[SimulationStep, ...]:
        _ensure_engine(config, self.engine)
        return tuple(
            _step_from_evaluation(self.engine, self._engine.evaluate(request))
            for request in self._requests
            if config.strategy_id is None or config.strategy_id == _bonus_strategy_id(request)
        )


class SportsCapitalSimulationAdapter:
    """Evaluates SportsCapitalEngine requests as isolated simulation steps."""

    engine = SimulationEngine.SPORTS_CAPITAL
    is_placeholder = False

    def __init__(self, requests: tuple[SportsCapitalEngineRequest, ...]) -> None:
        self._requests = requests
        self._engine = SportsCapitalEngine()

    def build_steps(self, config: SimulationRunConfig) -> tuple[SimulationStep, ...]:
        _ensure_engine(config, self.engine)
        return tuple(
            _step_from_evaluation(self.engine, self._engine.evaluate(request))
            for request in self._requests
            if config.strategy_id is None
            or config.strategy_id == _sports_capital_strategy_id(request)
        )


class _PlaceholderSimulationAdapter:
    engine: SimulationEngine
    is_placeholder = True

    def build_steps(self, config: SimulationRunConfig) -> tuple[SimulationStep, ...]:
        _ensure_engine(config, self.engine)
        return (
            SimulationStep(
                id=f"{self.engine}-placeholder",
                capital_change=Decimal(0),
                description=f"{self.engine} sandbox placeholder",
            ),
        )


class YieldSimulationAdapter(_PlaceholderSimulationAdapter):
    """Explicit v1 sandbox adapter for the future Yield Engine."""

    engine = SimulationEngine.YIELD


class AlphaSimulationAdapter(_PlaceholderSimulationAdapter):
    """Explicit v1 sandbox adapter for the future Alpha Engine."""

    engine = SimulationEngine.ALPHA


def _ensure_engine(config: SimulationRunConfig, engine: SimulationEngine) -> None:
    if config.engine != engine:
        raise ValueError(f"{engine} adapter cannot simulate {config.engine}")


def _bonus_strategy_id(request: BonusEngineRequest) -> str:
    return "qualifying_bet" if isinstance(request.inputs, QualifyingBetInput) else "free_bet"


def _sports_capital_strategy_id(request: SportsCapitalEngineRequest) -> str:
    return "two_way_arbitrage" if isinstance(request.inputs, TwoWayArbitrageInput) else "dutching"


def _step_from_evaluation(
    engine: SimulationEngine,
    evaluation: BonusEngineEvaluation | SportsCapitalEngineEvaluation,
) -> SimulationStep:
    simulation_evaluation = SimulationEvaluation(
        strategy_result=evaluation.strategy_result,
        calculation_result=evaluation.calculation_result,
        worst_case_profit_loss=evaluation.worst_case_profit_loss,
        is_profitable=evaluation.is_profitable,
    )
    return SimulationStep(
        id=f"{engine}:{evaluation.strategy_result.opportunity_id}",
        capital_change=simulation_evaluation.worst_case_profit_loss,
        description=f"{engine} {evaluation.strategy_result.strategy}",
        evaluation=simulation_evaluation,
    )
