from datetime import timedelta
from decimal import Decimal

from qbet.calculations import QualifyingBetInput
from qbet.engine import BaseEngine, BaseEngineRequest, BaseStrategy
from qbet.orchestrator import (
    BaseEngineAdapter, CapitalOrchestrator, CapitalSnapshot, EngineCandidate, EngineId, OrchestratorConfig,
    RejectionReason, SandboxEngineAdapter,
)


def candidate(identifier: str, engine: EngineId, required: str, value: str, *, risk: str = "0.2", liquidity: str = "0.8", currency: str = "EUR", sandbox: bool = False) -> EngineCandidate:
    return EngineCandidate(id=identifier, engine=engine, required_capital=Decimal(required), expected_value=Decimal(value), roi=Decimal(value) / Decimal(required), risk_score=Decimal(risk), liquidity_score=Decimal(liquidity), capital_lock_up=timedelta(hours=1), currency=currency, is_sandbox=sandbox)


def test_ranks_candidates_deterministically_and_allocates_without_side_effects() -> None:
    adapters = (
        SandboxEngineAdapter(candidate("yield", EngineId.YIELD, "30", "4", sandbox=True)),
        SandboxEngineAdapter(candidate("alpha", EngineId.ALPHA, "40", "6", sandbox=True)),
    )
    result = CapitalOrchestrator().allocate(CapitalSnapshot(available_capital=Decimal("100"), currency="EUR"), OrchestratorConfig(max_risk_score=Decimal("0.5"), min_liquidity_score=Decimal("0.5")), adapters)
    assert tuple(item.candidate.id for item in result.allocations) == ("alpha", "yield")
    assert result.remaining_capital == Decimal("30")
    assert all(item.candidate.is_sandbox for item in result.allocations)


def test_rejects_candidates_with_inspectable_limit_reasons() -> None:
    adapters = (
        SandboxEngineAdapter(candidate("risk", EngineId.YIELD, "10", "9", risk="0.9", sandbox=True)),
        SandboxEngineAdapter(candidate("liquidity", EngineId.ALPHA, "10", "8", liquidity="0.1", sandbox=True)),
        SandboxEngineAdapter(candidate("capital", EngineId.YIELD, "200", "7", sandbox=True)),
    )
    result = CapitalOrchestrator().allocate(CapitalSnapshot(available_capital=Decimal("100"), currency="EUR"), OrchestratorConfig(max_risk_score=Decimal("0.5"), min_liquidity_score=Decimal("0.5")), adapters)
    assert {item.candidate.id: item.reason for item in result.rejections} == {"risk": RejectionReason.RISK_LIMIT, "liquidity": RejectionReason.LIQUIDITY_LIMIT, "capital": RejectionReason.CAPITAL_LIMIT}


def test_sandbox_adapter_refuses_real_candidate() -> None:
    try:
        SandboxEngineAdapter(candidate("bad", EngineId.YIELD, "10", "1"))
    except ValueError as error:
        assert "sandbox" in str(error)
    else:
        raise AssertionError("expected sandbox adapter to reject a real candidate")

def test_base_engine_adapter_preserves_a_real_base_evaluation() -> None:
    evaluation = BaseEngine().evaluate(
        BaseEngineRequest(
            opportunity_id="opportunity-1",
            strategy=BaseStrategy.QUALIFYING_BET,
            inputs=QualifyingBetInput(
                back_odds=Decimal("2.5"), lay_odds=Decimal("2.6"), back_stake=Decimal("10"),
                exchange_commission=Decimal("0.02"), stake_precision=Decimal("0.01"),
                max_lay_liability=Decimal("100"),
            ),
            currency="EUR",
            execution_offer_ids=("bookmaker", "exchange"),
        )
    )
    adapter = BaseEngineAdapter(evaluation, risk_score=Decimal("0.2"), liquidity_score=Decimal("0.8"), capital_lock_up=timedelta(hours=1))

    result = CapitalOrchestrator().allocate(
        CapitalSnapshot(available_capital=Decimal("100"), currency="EUR"),
        OrchestratorConfig(max_risk_score=Decimal("0.5"), min_liquidity_score=Decimal("0.5")),
        (adapter,),
    )

    assert result.allocations[0].candidate.engine is EngineId.BASE
    assert result.allocations[0].candidate.source_evaluation == evaluation
    assert result.allocations[0].allocated_capital == Decimal("25.504")

def test_base_candidate_is_rejected_when_hedge_cash_exceeds_snapshot() -> None:
    evaluation = BaseEngine().evaluate(
        BaseEngineRequest(opportunity_id="opportunity-2", strategy=BaseStrategy.QUALIFYING_BET, inputs=QualifyingBetInput(back_odds=Decimal("2.5"), lay_odds=Decimal("2.6"), back_stake=Decimal("10"), exchange_commission=Decimal("0.02"), stake_precision=Decimal("0.01"), max_lay_liability=Decimal("100")), currency="EUR", execution_offer_ids=("bookmaker", "exchange"))
    )
    result = CapitalOrchestrator().allocate(CapitalSnapshot(available_capital=Decimal("20"), currency="EUR"), OrchestratorConfig(max_risk_score=Decimal("0.5"), min_liquidity_score=Decimal("0.5")), (BaseEngineAdapter(evaluation, risk_score=Decimal("0.2"), liquidity_score=Decimal("0.8"), capital_lock_up=timedelta(hours=1)),))
    assert result.rejections[0].reason is RejectionReason.CAPITAL_LIMIT