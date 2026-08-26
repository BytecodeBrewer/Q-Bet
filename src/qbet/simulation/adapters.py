"""Sandbox engine adapters sharing the simulation runner contract."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from qbet.simulation.models import SimulationEngine, SimulationRunConfig, SimulationStep


class SimulationEngineAdapter(Protocol):
    """Supply deterministic simulation steps for one Q-Bet engine."""

    engine: SimulationEngine
    is_placeholder: bool

    def build_steps(self, config: SimulationRunConfig) -> tuple[SimulationStep, ...]:
        """Build engine-specific mocked steps without external actions."""
        ...


class _PlaceholderSimulationAdapter:
    engine: SimulationEngine
    is_placeholder = True

    def build_steps(self, config: SimulationRunConfig) -> tuple[SimulationStep, ...]:
        if config.engine != self.engine:
            raise ValueError(f"{self.engine} adapter cannot simulate {config.engine}")
        return (
            SimulationStep(
                id=f"{self.engine}-placeholder",
                capital_change=Decimal("0"),
                description=f"{self.engine} sandbox placeholder",
            ),
        )


class YieldSimulationAdapter(_PlaceholderSimulationAdapter):
    """Explicit v1 sandbox adapter for the future Yield Engine."""

    engine = SimulationEngine.YIELD


class AlphaSimulationAdapter(_PlaceholderSimulationAdapter):
    """Explicit v1 sandbox adapter for the future Alpha Engine."""

    engine = SimulationEngine.ALPHA