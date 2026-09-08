"""Deterministic Phase-2 pipeline readiness contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from qbet.workflow.models import WorkflowMode, WorkflowStage
from qbet.workflow.routing import V1Engine

PIPELINE_STAGES: tuple[WorkflowStage, ...] = (
    WorkflowStage.DATA_AGGREGATION,
    WorkflowStage.ENGINE_PREPARATION,
    WorkflowStage.CALCULATION,
    WorkflowStage.DOMAIN_RISK,
    WorkflowStage.LIQUIDITY_CHECK,
    WorkflowStage.DISPATCH,
)


@dataclass(frozen=True)
class StageReadiness:
    stage: WorkflowStage
    ready: bool
    reason: str | None = None


class PipelineReadinessProvider(Protocol):
    def snapshot(
        self,
        engine: V1Engine,
        mode: WorkflowMode,
    ) -> tuple[StageReadiness, ...]: ...


class Phase2PipelineReadiness:
    """Current deterministic/local Phase-2 components are ready when activated.

    Real provider/API health arrives in Phase 3. Keeping readiness behind a protocol lets
    those checks replace this deterministic baseline without changing the dispatcher or GUI.
    """

    def snapshot(
        self,
        engine: V1Engine,
        mode: WorkflowMode,
    ) -> tuple[StageReadiness, ...]:
        del engine, mode
        return tuple(StageReadiness(stage=stage, ready=True) for stage in PIPELINE_STAGES)
