from datetime import timedelta
from decimal import Decimal

from qbet.orchestrator import CapitalOrchestrator, CapitalSnapshot, EngineCandidate, EngineId, OrchestratorConfig, RejectionReason, SandboxEngineAdapter


def candidate(identifier: str, engine: EngineId, required: str, value: str, *, risk: str = "0.2", liquidity: str = "0.8", sandbox: bool = False) -> EngineCandidate:
    return EngineCandidate(id=identifier, engine=engine, required_capital=Decimal(required), expected_value=Decimal(value), roi=Decimal(value) / Decimal(required), risk_score=Decimal(risk), liquidity_score=Decimal(liquidity), capital_lock_up=timedelta(hours=1), currency="EUR", is_sandbox=sandbox)


def test_orchestrator_ranks_and_rejects_sandbox_candidates() -> None:
    adapters = (SandboxEngineAdapter(candidate("alpha", EngineId.ALPHA, "40", "6", sandbox=True)), SandboxEngineAdapter(candidate("risk", EngineId.YIELD, "10", "9", risk="0.9", sandbox=True)))
    result = CapitalOrchestrator().allocate(CapitalSnapshot(available_capital=Decimal("100"), currency="EUR"), OrchestratorConfig(max_risk_score=Decimal("0.5"), min_liquidity_score=Decimal("0.5")), adapters)
    assert result.allocations[0].candidate.id == "alpha"
    assert result.rejections[0].reason is RejectionReason.RISK_LIMIT