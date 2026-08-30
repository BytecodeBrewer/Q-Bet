from __future__ import annotations
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4
from pydantic import Field, model_validator
from qbet.domain.models import DomainModel, Identifier
from qbet.layers.logging import SimulationLogRecord
class WorkflowStage(StrEnum):
    DATA_AGGREGATION = "data_aggregation"
    ENGINE_PREPARATION = "engine_preparation"
    CALCULATION = "calculation"
    DOMAIN_RISK = "domain_risk"
    LIQUIDITY_CHECK = "liquidity_check"
    DISPATCH = "dispatch"
class WorkflowMode(StrEnum):
    SIMULATION = "simulation"
    EXECUTION = "execution"
class WorkflowDecision(StrEnum):
    ALLOW = "allow"
    REJECT = "reject"
    RECHECK = "recheck"
class WorkflowStageDecision(DomainModel):
    decision: WorkflowDecision
    reason: str | None = None
    @model_validator(mode="after")
    def requires_reason(self) -> "WorkflowStageDecision":
        if self.decision is not WorkflowDecision.ALLOW and not self.reason:
            raise ValueError("rejected or recheck decisions require a reason")
        return self
class WorkflowRequest(DomainModel):
    id: Identifier
    mode: WorkflowMode
    stages: tuple[WorkflowStage, ...] = Field(min_length=1)
    correlation_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
class WorkflowContext(DomainModel):
    request: WorkflowRequest
    correlation_id: UUID
    stage: WorkflowStage
class WorkflowTransition(DomainModel):
    sequence: int = Field(gt=0)
    correlation_id: UUID
    stage: WorkflowStage
    decision: WorkflowDecision
    reason: str | None = None
class WorkflowResult(DomainModel):
    correlation_id: UUID
    mode: WorkflowMode
    final_decision: WorkflowDecision
    transitions: tuple[WorkflowTransition, ...]
    log_records: tuple[SimulationLogRecord, ...]
def new_correlation_id() -> UUID:
    return uuid4()
