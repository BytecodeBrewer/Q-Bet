from datetime import timedelta
from decimal import Decimal

import pytest

from qbet.calculations import ArbitrageOffer, QualifyingBetInput, TwoWayArbitrageInput
from qbet.domain.verification import ProviderState
from qbet.engines import BonusEngine, BonusEngineRequest, SportsCapitalEngine, SportsCapitalEngineRequest
from qbet.orchestrator import (
    CapitalOrchestrator,
    CapitalSnapshot,
    EngineCandidate,
    EngineId,
    OrchestratorConfig,
    RejectionReason,
    SandboxEngineAdapter,
    VerifiedStrategyCandidateAdapter,
)


def candidate(
    identifier: str,
    engine: EngineId,
    required: str,
    value: str,
    *,
    risk: str = "0.2",
    liquidity: str = "0.8",
    sandbox: bool = False,
) -> EngineCandidate:
    return EngineCandidate(
        id=identifier,
        engine=engine,
        required_capital=Decimal(required),
        expected_value=Decimal(value),
        roi=Decimal(value) / Decimal(required),
        risk_score=Decimal(risk),
        liquidity_score=Decimal(liquidity),
        capital_lock_up=timedelta(hours=1),
        currency="EUR",
        is_sandbox=sandbox,
    )


def snapshot() -> CapitalSnapshot:
    return CapitalSnapshot(available_capital=Decimal("1000"), currency="EUR")


def config() -> OrchestratorConfig:
    return OrchestratorConfig(max_risk_score=Decimal("0.5"), min_liquidity_score=Decimal("0.5"))


def provider_state(**changes: object) -> ProviderState:
    values = {"provider_id": "book", "active_bets_count": 0, "is_cooldown_active": False}
    values.update(changes)
    return ProviderState(**values)


def bonus_adapter(*, state: ProviderState | None = None, risk: str = "0.2", evaluation_opportunity_id: str | None = None) -> VerifiedStrategyCandidateAdapter:
    request = BonusEngineRequest(
        opportunity_id="bonus-opportunity",
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100"),
        ),
        currency="EUR",
        execution_offer_ids=("book", "exchange"),
    )
    return VerifiedStrategyCandidateAdapter(
        request,
        BonusEngine().evaluate(request.model_copy(update={"opportunity_id": evaluation_opportunity_id}) if evaluation_opportunity_id else request),
        state or provider_state(),
        risk_score=Decimal(risk),
        liquidity_score=Decimal("0.8"),
        capital_lock_up=timedelta(hours=1),
    )


def sports_adapter(*, state: ProviderState | None = None, evaluation_opportunity_id: str | None = None) -> VerifiedStrategyCandidateAdapter:
    def offer(outcome: str) -> ArbitrageOffer:
        return ArbitrageOffer(
            outcome=outcome,
            odds=Decimal("2.2"),
            available_liquidity=Decimal("100"),
            stake_precision=Decimal("0.01"),
            currency="EUR",
        )

    request = SportsCapitalEngineRequest(
        opportunity_id="sports-opportunity",
        inputs=TwoWayArbitrageInput(
            first_offer=offer("home"),
            second_offer=offer("away"),
            requested_total_stake=Decimal("100"),
        ),
        currency="EUR",
        execution_offer_ids=("home", "away"),
    )
    return VerifiedStrategyCandidateAdapter(
        request,
        SportsCapitalEngine().evaluate(request.model_copy(update={"opportunity_id": evaluation_opportunity_id}) if evaluation_opportunity_id else request),
        state or provider_state(),
        risk_score=Decimal("0.2"),
        liquidity_score=Decimal("0.8"),
        capital_lock_up=timedelta(hours=1),
    )


def test_orchestrator_ranks_and_rejects_sandbox_candidates() -> None:
    adapters = (
        SandboxEngineAdapter(candidate("alpha", EngineId.ALPHA, "40", "6", sandbox=True)),
        SandboxEngineAdapter(candidate("risk", EngineId.YIELD, "10", "9", risk="0.9", sandbox=True)),
    )

    result = CapitalOrchestrator().allocate(snapshot(), config(), adapters)

    assert result.allocations[0].candidate.id == "alpha"
    assert result.rejections[0].reason is RejectionReason.RISK_LIMIT


def test_verified_bonus_and_sports_candidates_reach_the_existing_allocator() -> None:
    result = CapitalOrchestrator().allocate(snapshot(), config(), (bonus_adapter(), sports_adapter()))

    assert {allocation.candidate.engine for allocation in result.allocations} == {
        EngineId.BONUS,
        EngineId.SPORTS_CAPITAL,
    }
    assert result.rejections == ()


def test_provider_frequency_rejection_prevents_bonus_allocation() -> None:
    result = CapitalOrchestrator().allocate(
        snapshot(),
        config(),
        (bonus_adapter(state=provider_state(active_bets_count=2)),),
    )

    assert result.allocations == ()
    assert result.rejections[0].reason is RejectionReason.PROVIDER_FREQUENCY_LIMIT


def test_provider_cooldown_rejection_prevents_sports_allocation() -> None:
    result = CapitalOrchestrator().allocate(
        snapshot(),
        config(),
        (sports_adapter(state=provider_state(is_cooldown_active=True)),),
    )

    assert result.allocations == ()
    assert result.rejections[0].reason is RejectionReason.PROVIDER_COOLDOWN_ACTIVE


def test_existing_allocator_limits_still_apply_after_verification() -> None:
    result = CapitalOrchestrator().allocate(snapshot(), config(), (bonus_adapter(risk="0.9"),))

    assert result.allocations == ()
    assert result.rejections[0].reason is RejectionReason.RISK_LIMIT


def test_verified_strategy_adapter_rejects_a_different_bonus_opportunity() -> None:
    with pytest.raises(ValueError, match="same opportunity"):
        bonus_adapter(evaluation_opportunity_id="different-bonus-opportunity")


def test_verified_strategy_adapter_rejects_a_different_sports_opportunity() -> None:
    with pytest.raises(ValueError, match="same opportunity"):
        sports_adapter(evaluation_opportunity_id="different-sports-opportunity")
