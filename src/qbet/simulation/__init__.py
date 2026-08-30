"""Deterministic simulation contracts and sandbox engine adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .adapters import (
    AlphaSimulationAdapter,
    BonusSimulationAdapter,
    SimulationEngineAdapter,
    SportsCapitalSimulationAdapter,
    YieldSimulationAdapter,
)
from .models import (
    SimulationContext,
    SimulationEngine,
    SimulationEvaluation,
    SimulationEvent,
    SimulationEventType,
    SimulationResult,
    SimulationRunConfig,
    SimulationStatus,
    SimulationStep,
    SimulationTopUpEvent,
)
from .runner import DeterministicSimulationRunner, SimulationRunner

if TYPE_CHECKING:
    from .reporting import ReportingSimulationRunner


__all__ = [
    "AlphaSimulationAdapter",
    "BonusSimulationAdapter",
    "DeterministicSimulationRunner",
    "ReportingSimulationRunner",
    "SimulationContext",
    "SimulationEngine",
    "SimulationEngineAdapter",
    "SimulationEvaluation",
    "SimulationEvent",
    "SimulationEventType",
    "SimulationResult",
    "SimulationRunConfig",
    "SimulationRunner",
    "SimulationStatus",
    "SimulationStep",
    "SimulationTopUpEvent",
    "SportsCapitalSimulationAdapter",
    "YieldSimulationAdapter",
    "WorkflowSimulationRequest",
    "WorkflowSimulationResult",
    "WorkflowSimulationRunner",
]


def __getattr__(name: str) -> object:
    if name in {"WorkflowSimulationRequest", "WorkflowSimulationResult", "WorkflowSimulationRunner"}:
        from .workflow import WorkflowSimulationRequest, WorkflowSimulationResult, WorkflowSimulationRunner
        return {"WorkflowSimulationRequest": WorkflowSimulationRequest, "WorkflowSimulationResult": WorkflowSimulationResult, "WorkflowSimulationRunner": WorkflowSimulationRunner}[name]
    if name == "ReportingSimulationRunner":
        from .reporting import ReportingSimulationRunner

        return ReportingSimulationRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
