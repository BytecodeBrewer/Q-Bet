"""Deterministic provider-operation verification boundary."""
from qbet.domain.verification import ProviderState, SportsOpportunityRequest, VerificationResult

FREQUENCY_LIMIT_REASON = "provider_frequency_limit"
COOLDOWN_ACTIVE_REASON = "provider_cooldown_active"

class OperationalRiskLayer:
    """Applies provider frequency and cooldown guardrails without side effects."""
    def verify_opportunity(self, request: SportsOpportunityRequest, provider_state: ProviderState) -> VerificationResult:
        if provider_state.active_bets_count >= 2:
            return VerificationResult(is_allowed=False, rejection_reason=FREQUENCY_LIMIT_REASON)
        if provider_state.is_cooldown_active:
            return VerificationResult(is_allowed=False, rejection_reason=COOLDOWN_ACTIVE_REASON)
        return VerificationResult(is_allowed=True)