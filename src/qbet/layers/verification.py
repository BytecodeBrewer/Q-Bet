"""Deterministic provider-operation verification boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from qbet.domain.models import Identifier
from qbet.domain.verification import (
    DomainRiskDecisionCode,
    DomainRiskPolicy,
    DomainRiskStatus,
    ProviderState,
    SportsOpportunityRequest,
    VerificationResult,
)
from qbet.layers.logging import SimulationLogContext, SimulationLogRecordType

if TYPE_CHECKING:
    from qbet.storage.protocol import ProviderStateRepository


FREQUENCY_LIMIT_REASON = DomainRiskDecisionCode.FREQUENCY_LIMIT.value
COOLDOWN_ACTIVE_REASON = DomainRiskDecisionCode.COOLDOWN_ACTIVE.value


class OperationalRiskLayer:
    """Applies persisted or supplied provider guardrails without side effects."""

    def __init__(
        self,
        policy: DomainRiskPolicy | None = None,
        *,
        provider_state_repository: ProviderStateRepository | None = None,
    ) -> None:
        self._policy = policy or DomainRiskPolicy()
        self._provider_state_repository = provider_state_repository

    def verify_opportunity(
        self,
        request: SportsOpportunityRequest,
        provider_state: ProviderState | None = None,
        *,
        provider_id: Identifier | None = None,
        log_context: SimulationLogContext | None = None,
        correlation_id: UUID | None = None,
    ) -> VerificationResult:
        resolved_state = self._resolve_provider_state(provider_state, provider_id)
        result = (
            self._evaluate(resolved_state)
            if resolved_state is not None
            else VerificationResult(
                is_allowed=False,
                status=DomainRiskStatus.RECHECK,
                decision_code=DomainRiskDecisionCode.RECHECK_REQUIRED,
            )
        )
        if log_context is not None:
            if correlation_id is not None and log_context.run_id != correlation_id:
                raise ValueError("log context and correlation_id must match")
            log_context.record(
                SimulationLogRecordType.RISK_DECISION,
                "layers.operational_risk",
                {
                    "opportunity_id": request.opportunity_id,
                    "provider_id": (
                        resolved_state.provider_id
                        if resolved_state is not None
                        else provider_id
                    ),
                    "status": result.status,
                    "decision_code": result.decision_code,
                    "warning_codes": result.warning_codes,
                },
            )
        return result

    def _resolve_provider_state(
        self,
        provider_state: ProviderState | None,
        provider_id: Identifier | None,
    ) -> ProviderState | None:
        if provider_state is not None:
            return provider_state
        if provider_id is None:
            raise ValueError("provider_id is required when provider_state is not supplied")
        if self._provider_state_repository is None:
            raise ValueError("provider state repository is required when provider_state is not supplied")
        return self._provider_state_repository.get(provider_id)

    def _evaluate(self, provider_state: ProviderState) -> VerificationResult:
        if provider_state.active_bets_count >= self._policy.active_bet_rejection_threshold:
            return VerificationResult(
                is_allowed=False,
                status=DomainRiskStatus.REJECT,
                decision_code=DomainRiskDecisionCode.FREQUENCY_LIMIT,
                rejection_reason=FREQUENCY_LIMIT_REASON,
            )
        if provider_state.is_cooldown_active:
            return VerificationResult(
                is_allowed=False,
                status=DomainRiskStatus.REJECT,
                decision_code=DomainRiskDecisionCode.COOLDOWN_ACTIVE,
                rejection_reason=COOLDOWN_ACTIVE_REASON,
            )
        if provider_state.active_bets_count >= self._policy.active_bet_warning_threshold:
            return VerificationResult(
                is_allowed=True,
                status=DomainRiskStatus.WARN,
                decision_code=DomainRiskDecisionCode.ACTIVE_BET_LIMIT_APPROACHING,
                warning_codes=(DomainRiskDecisionCode.ACTIVE_BET_LIMIT_APPROACHING,),
            )
        return VerificationResult(is_allowed=True)
