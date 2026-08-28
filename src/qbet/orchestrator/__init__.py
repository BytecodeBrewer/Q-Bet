"""Deterministic, proposal-only capital orchestration."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, TypeAlias

from pydantic import Field, model_validator

from qbet.calculations import FreeBetResult, QualifyingBetResult
from qbet.domain.models import Currency, DomainModel, Identifier, PositiveDecimal
from qbet.domain.verification import ProviderState, SportsOpportunityRequest, VerificationResult
from qbet.engines import (
    BonusEngineEvaluation,
    BonusEngineRequest,
    SportsCapitalEngineEvaluation,
    SportsCapitalEngineRequest,
)
from qbet.layers import OperationalRiskLayer


class EngineId(StrEnum):
    BONUS = "bonus"
    SPORTS_CAPITAL = "sports_capital"
    YIELD = "yield"
    ALPHA = "alpha"


class RejectionReason(StrEnum):
    PROVIDER_FREQUENCY_LIMIT = "provider_frequency_limit"
    PROVIDER_COOLDOWN_ACTIVE = "provider_cooldown_active"
    CURRENCY_MISMATCH = "currency_mismatch"
    RISK_LIMIT = "risk_limit"
    LIQUIDITY_LIMIT = "liquidity_limit"
    CAPITAL_LIMIT = "capital_limit"


class CapitalSnapshot(DomainModel):
    available_capital: PositiveDecimal
    currency: Currency


class OrchestratorConfig(DomainModel):
    max_risk_score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    min_liquidity_score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))


StrategyRequest: TypeAlias = BonusEngineRequest | SportsCapitalEngineRequest
StrategyEvaluation: TypeAlias = BonusEngineEvaluation | SportsCapitalEngineEvaluation


class EngineCandidate(DomainModel):
    id: Identifier
    engine: EngineId
    required_capital: PositiveDecimal
    expected_value: Decimal
    roi: Decimal
    risk_score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    liquidity_score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    capital_lock_up: timedelta
    currency: Currency
    is_sandbox: bool = False
    source_evaluation: StrategyEvaluation | None = None
    verification_result: VerificationResult | None = None

    @model_validator(mode="after")
    def candidate_is_safe_and_bounded(self) -> "EngineCandidate":
        if self.capital_lock_up < timedelta(0):
            raise ValueError("capital_lock_up must not be negative")
        if self.engine in {EngineId.BONUS, EngineId.SPORTS_CAPITAL}:
            if self.source_evaluation is None:
                raise ValueError("strategy candidates require a strategy evaluation")
            if self.verification_result is None:
                raise ValueError("strategy candidates require operational verification")
        elif not self.is_sandbox:
            raise ValueError("non-strategy candidates must remain sandbox candidates")
        return self


class Allocation(DomainModel):
    candidate: EngineCandidate
    allocated_capital: PositiveDecimal


class CandidateRejection(DomainModel):
    candidate: EngineCandidate
    reason: RejectionReason


class OrchestrationResult(DomainModel):
    allocations: tuple[Allocation, ...]
    rejections: tuple[CandidateRejection, ...]
    remaining_capital: Decimal = Field(ge=Decimal("0"))


class EngineAdapter(Protocol):
    """Produces proposal-only candidates for a shared capital snapshot."""

    def evaluate_candidates(self, snapshot: CapitalSnapshot) -> tuple[EngineCandidate, ...]: ...


class VerifiedStrategyCandidateAdapter:
    """Adapts an evaluated concrete strategy after Layer 2 verification."""

    def __init__(
        self,
        request: StrategyRequest,
        evaluation: StrategyEvaluation,
        provider_state: ProviderState,
        *,
        risk_score: Decimal,
        liquidity_score: Decimal,
        capital_lock_up: timedelta,
        risk_layer: OperationalRiskLayer | None = None,
    ) -> None:
        if isinstance(request, BonusEngineRequest) != isinstance(evaluation, BonusEngineEvaluation):
            raise ValueError("request and evaluation must belong to the same engine")
        if isinstance(request, SportsCapitalEngineRequest) != isinstance(evaluation, SportsCapitalEngineEvaluation):
            raise ValueError("request and evaluation must belong to the same engine")
        self._request = request
        self._evaluation = evaluation
        self._provider_state = provider_state
        self._risk_score = risk_score
        self._liquidity_score = liquidity_score
        self._capital_lock_up = capital_lock_up
        self._risk_layer = risk_layer or OperationalRiskLayer()

    def evaluate_candidates(self, snapshot: CapitalSnapshot) -> tuple[EngineCandidate, ...]:
        verification_result = self._risk_layer.verify_opportunity(self._request, self._provider_state)
        return (
            EngineCandidate(
                id=f"{_engine_id_for(self._evaluation)}:{self._evaluation.strategy_result.opportunity_id}",
                engine=_engine_id_for(self._evaluation),
                required_capital=_strategy_required_capital(self._evaluation),
                expected_value=self._evaluation.worst_case_profit_loss,
                roi=self._evaluation.worst_case_profit_loss / self._evaluation.strategy_result.stake,
                risk_score=self._risk_score,
                liquidity_score=self._liquidity_score,
                capital_lock_up=self._capital_lock_up,
                currency=self._evaluation.strategy_result.currency,
                source_evaluation=self._evaluation,
                verification_result=verification_result,
            ),
        )


class SandboxEngineAdapter:
    """Deterministic placeholder adapter for a future Yield or Alpha engine."""

    def __init__(self, candidate: EngineCandidate) -> None:
        if not candidate.is_sandbox:
            raise ValueError("sandbox adapters require sandbox candidates")
        self._candidate = candidate

    def evaluate_candidates(self, snapshot: CapitalSnapshot) -> tuple[EngineCandidate, ...]:
        return (self._candidate,)


class CapitalOrchestrator:
    """Ranks compatible candidates and produces allocations without side effects."""

    def allocate(
        self,
        snapshot: CapitalSnapshot,
        config: OrchestratorConfig,
        adapters: tuple[EngineAdapter, ...],
    ) -> OrchestrationResult:
        candidates = tuple(candidate for adapter in adapters for candidate in adapter.evaluate_candidates(snapshot))
        ordered = sorted(
            candidates,
            key=lambda candidate: (
                -candidate.expected_value,
                -candidate.roi,
                candidate.risk_score,
                -candidate.liquidity_score,
                candidate.capital_lock_up,
                candidate.id,
            ),
        )
        remaining = snapshot.available_capital
        allocations: list[Allocation] = []
        rejections: list[CandidateRejection] = []
        for candidate in ordered:
            reason = _rejection_reason(candidate, snapshot, config, remaining)
            if reason is not None:
                rejections.append(CandidateRejection(candidate=candidate, reason=reason))
                continue
            allocations.append(Allocation(candidate=candidate, allocated_capital=candidate.required_capital))
            remaining -= candidate.required_capital
        return OrchestrationResult(
            allocations=tuple(allocations),
            rejections=tuple(rejections),
            remaining_capital=remaining,
        )


def _rejection_reason(
    candidate: EngineCandidate,
    snapshot: CapitalSnapshot,
    config: OrchestratorConfig,
    remaining: Decimal,
) -> RejectionReason | None:
    if candidate.verification_result is not None and not candidate.verification_result.is_allowed:
        assert candidate.verification_result.rejection_reason is not None
        return RejectionReason(candidate.verification_result.rejection_reason)
    if candidate.currency != snapshot.currency:
        return RejectionReason.CURRENCY_MISMATCH
    if candidate.risk_score > config.max_risk_score:
        return RejectionReason.RISK_LIMIT
    if candidate.liquidity_score < config.min_liquidity_score:
        return RejectionReason.LIQUIDITY_LIMIT
    if candidate.required_capital > remaining:
        return RejectionReason.CAPITAL_LIMIT
    return None


def _engine_id_for(evaluation: StrategyEvaluation) -> EngineId:
    if isinstance(evaluation, BonusEngineEvaluation):
        return EngineId.BONUS
    return EngineId.SPORTS_CAPITAL


def _strategy_required_capital(evaluation: StrategyEvaluation) -> Decimal:
    result = evaluation.calculation_result
    if isinstance(result, QualifyingBetResult):
        return result.back_stake + result.lay_liability
    if isinstance(result, FreeBetResult):
        return result.lay_liability
    return evaluation.strategy_result.stake