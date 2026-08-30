"""Deterministic provider-operation verification boundary."""
from uuid import UUID
from qbet.domain.verification import DomainRiskDecisionCode, DomainRiskPolicy, DomainRiskStatus, ProviderState, SportsOpportunityRequest, VerificationResult
from qbet.layers.logging import SimulationLogContext, SimulationLogRecordType

FREQUENCY_LIMIT_REASON = DomainRiskDecisionCode.FREQUENCY_LIMIT.value
COOLDOWN_ACTIVE_REASON = DomainRiskDecisionCode.COOLDOWN_ACTIVE.value

class OperationalRiskLayer:
    """Applies provider frequency and cooldown guardrails without side effects."""

    def __init__(self, policy: DomainRiskPolicy | None = None) -> None:
        self._policy = policy or DomainRiskPolicy()

    def verify_opportunity(
        self,
        request: SportsOpportunityRequest,
        provider_state: ProviderState,
        *,
        log_context: SimulationLogContext | None = None,
        correlation_id: UUID | None = None,
    ) -> VerificationResult:
        result = self._evaluate(provider_state)
        if log_context is not None:
            if correlation_id is not None and log_context.run_id != correlation_id:
                raise ValueError("log context and correlation_id must match")
            log_context.record(
                SimulationLogRecordType.RISK_DECISION,
                "layers.operational_risk",
                {
                    "opportunity_id": request.opportunity_id,
                    "provider_id": provider_state.provider_id,
                    "status": result.status,
                    "decision_code": result.decision_code,
                    "warning_codes": result.warning_codes,
                },
            )
        return result

    def _evaluate(self, provider_state: ProviderState) -> VerificationResult:
        if provider_state.active_bets_count >= self._policy.active_bet_rejection_threshold:
            return VerificationResult(is_allowed=False, status=DomainRiskStatus.REJECT, decision_code=DomainRiskDecisionCode.FREQUENCY_LIMIT, rejection_reason=FREQUENCY_LIMIT_REASON)
        if provider_state.is_cooldown_active:
            return VerificationResult(is_allowed=False, status=DomainRiskStatus.REJECT, decision_code=DomainRiskDecisionCode.COOLDOWN_ACTIVE, rejection_reason=COOLDOWN_ACTIVE_REASON)
        if provider_state.active_bets_count >= self._policy.active_bet_warning_threshold:
            return VerificationResult(is_allowed=True, status=DomainRiskStatus.WARN, decision_code=DomainRiskDecisionCode.ACTIVE_BET_LIMIT_APPROACHING, warning_codes=(DomainRiskDecisionCode.ACTIVE_BET_LIMIT_APPROACHING,))
        return VerificationResult(is_allowed=True)
