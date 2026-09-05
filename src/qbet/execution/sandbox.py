"""Re-evaluate engine inputs locally; never contact external providers."""

from datetime import datetime

from qbet.calculations import FreeBetResult, QualifyingBetResult
from qbet.engines import BonusEngine, BonusEngineRequest, SportsCapitalEngine
from qbet.execution.models import ApprovedExecutionRequest, ExecutionProposal, SandboxResult


def valuation(request):
    evaluation = (
        BonusEngine().evaluate(request)
        if isinstance(request, BonusEngineRequest)
        else SportsCapitalEngine().evaluate(request)
    )
    result = evaluation.calculation_result
    if isinstance(result, QualifyingBetResult):
        capital = result.back_stake + result.lay_liability
    elif isinstance(result, FreeBetResult):
        capital = result.lay_liability
    else:
        capital = result.total_stake
    return capital, capital + evaluation.worst_case_profit_loss


class SandboxRequestHandler:
    def validate(self, proposal: ExecutionProposal, now: datetime) -> str | None:
        if now >= proposal.expires_at:
            return "proposal_expired"
        expected_engine = "bonus" if isinstance(proposal.request, BonusEngineRequest) else "sports_capital"
        if proposal.work.engine != expected_engine:
            return "engine_mismatch"
        if proposal.request.currency != proposal.currency:
            return "currency_mismatch"
        if proposal.request.opportunity_id != proposal.work.opportunity_id:
            return "opportunity_mismatch"
        capital, payout = valuation(proposal.request)
        if capital != proposal.capital_required or payout != proposal.payout:
            return "proposal_changed"
        return None

    def validate_result(self, proposal: ExecutionProposal, result: SandboxResult) -> str | None:
        if (
            result.dispatch_id != proposal.work.id
            or result.correlation_id != proposal.work.correlation_id
            or result.mode != proposal.work.mode.value
            or result.currency != proposal.currency
        ):
            return "result_identity_mismatch"
        if result.observed_at >= proposal.expires_at:
            return "stale_result"
        if result.status not in {"success", "failed", "cancelled"}:
            return "unknown_or_partial_result"
        if result.status == "success" and result.payout != proposal.payout:
            return "unexpected_payout"
        return None


class BonusSandboxAdapter:
    engine = "bonus"

    def dispatch(self, request: ApprovedExecutionRequest) -> SandboxResult:
        proposal = request.proposal
        if proposal.work.engine != self.engine:
            raise ValueError("wrong sandbox adapter")
        return SandboxResult(
            dispatch_id=proposal.work.id,
            correlation_id=proposal.work.correlation_id,
            mode=proposal.work.mode.value,
            currency=proposal.currency,
            payout=proposal.payout,
            status="success",
            observed_at=request.approved_at,
        )


class SportsCapitalSandboxAdapter(BonusSandboxAdapter):
    engine = "sports_capital"
