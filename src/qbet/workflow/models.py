from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from qbet.domain.models import DomainModel, Identifier
from qbet.layers.logging import SimulationLogRecord
from qbet.request_handler.models import RequestHandlerResult, TargetedMarketRevalidationContext


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


class WorkflowTransitionKind(StrEnum):
    STAGE = "stage"
    REFRESH = "refresh"


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
    opportunity_id: Identifier | None = None
    mode: WorkflowMode
    stages: tuple[WorkflowStage, ...] = Field(min_length=1)
    correlation_id: UUID | None = None
    market_revalidation: TargetedMarketRevalidationContext | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def resolved_opportunity_id(self) -> str:
        return self.opportunity_id or self.id

    @model_validator(mode="after")
    def validates_route(self) -> "WorkflowRequest":
        if len(set(self.stages)) != len(self.stages):
            raise ValueError("workflow stages must not contain duplicates")
        stage_positions = [list(WorkflowStage).index(stage) for stage in self.stages]
        if stage_positions != sorted(stage_positions):
            raise ValueError("workflow stages must follow pipeline order")
        if WorkflowStage.DISPATCH in self.stages:
            liquidity_position = (
                self.stages.index(WorkflowStage.LIQUIDITY_CHECK)
                if WorkflowStage.LIQUIDITY_CHECK in self.stages
                else -1
            )
            if liquidity_position < 0 or liquidity_position > self.stages.index(
                WorkflowStage.DISPATCH
            ):
                raise ValueError("dispatch requires a prior liquidity check")
        return self


class WorkflowContext(DomainModel):
    request: WorkflowRequest
    correlation_id: UUID
    stage: WorkflowStage


class WorkflowTransition(DomainModel):
    sequence: int = Field(gt=0)
    correlation_id: UUID
    stage: WorkflowStage
    kind: WorkflowTransitionKind = WorkflowTransitionKind.STAGE
    decision: WorkflowDecision
    reason: str | None = None


class WorkflowResult(DomainModel):
    correlation_id: UUID
    request_id: Identifier
    mode: WorkflowMode
    final_decision: WorkflowDecision
    transitions: tuple[WorkflowTransition, ...]
    log_records: tuple[SimulationLogRecord, ...]
    request_handler_result: RequestHandlerResult | None = None


def new_correlation_id() -> UUID:
    return uuid4()
