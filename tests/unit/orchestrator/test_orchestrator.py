from datetime import timedelta
from decimal import Decimal

import pytest

from qbet.calculations import ArbitrageOffer, QualifyingBetInput, TwoWayArbitrageInput
from qbet.domain.models import Currency
from qbet.domain.verification import ProviderState
from qbet.engines import (
    BonusEngine,
    BonusEngineRequest,
    SportsCapitalEngine,
    SportsCapitalEngineRequest,
)
from qbet.orchestrator import (
    CapitalSnapshot,
    EngineCandidate,
    EngineId,
    LiquidityChecker,
    OrchestratorConfig,
    RejectionReason,
    SandboxEngineAdapter,
    VerifiedStrategyCandidateAdapter,
)
from qbet.workflow import (
    WorkflowDecision,
    WorkflowMode,
    WorkflowOrchestrator,
    WorkflowRequest,
    WorkflowStage,
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
    currency: Currency = "EUR",
    lock_up: timedelta = timedelta(hours=1),
) -> EngineCandidate:
    return EngineCandidate(
        id=identifier,
        engine=engine,
        required_capital=Decimal(required),
        expected_value=Decimal(value),
        roi=Decimal(value) / Decimal(required),
        risk_score=Decimal(risk),
        liquidity_score=Decimal(liquidity),
        capital_lock_up=lock_up,
        currency=currency,
        is_sandbox=sandbox,
    )


def snapshot() -> CapitalSnapshot:
    return CapitalSnapshot(available_capital=Decimal(1000), currency="EUR")


def config() -> OrchestratorConfig:
    return OrchestratorConfig(
        max_risk_score=Decimal("0.5"), min_liquidity_score=Decimal("0.5")
    )


def provider_state(**changes: object) -> ProviderState:
    values = {
        "provider_id": "book",
        "active_bets_count": 0,
        "is_cooldown_active": False,
    }
    values.update(changes)
    return ProviderState.model_validate(values)


def bonus_adapter(
    *,
    state: ProviderState | None = None,
    risk: str = "0.2",
    evaluation_opportunity_id: str | None = None,
) -> VerifiedStrategyCandidateAdapter:
    request = BonusEngineRequest(
        opportunity_id="bonus-opportunity",
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal(10),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal(100),
        ),
        currency="EUR",
        execution_offer_ids=("book", "exchange"),
    )
    return VerifiedStrategyCandidateAdapter(
        request,
        BonusEngine().evaluate(
            request.model_copy(update={"opportunity_id": evaluation_opportunity_id})
            if evaluation_opportunity_id
            else request
        ),
        state or provider_state(),
        risk_score=Decimal(risk),
        liquidity_score=Decimal("0.8"),
        capital_lock_up=timedelta(hours=1),
    )


def sports_adapter(
    *, state: ProviderState | None = None, evaluation_opportunity_id: str | None = None
) -> VerifiedStrategyCandidateAdapter:
    def offer(outcome: str) -> ArbitrageOffer:
        return ArbitrageOffer(
            outcome=outcome,
            odds=Decimal("2.2"),
            available_liquidity=Decimal(100),
            stake_precision=Decimal("0.01"),
            currency="EUR",
        )

    request = SportsCapitalEngineRequest(
        opportunity_id="sports-opportunity",
        inputs=TwoWayArbitrageInput(
            first_offer=offer("home"),
            second_offer=offer("away"),
            requested_total_stake=Decimal(100),
        ),
        currency="EUR",
        execution_offer_ids=("home", "away"),
    )
    return VerifiedStrategyCandidateAdapter(
        request,
        SportsCapitalEngine().evaluate(
            request.model_copy(update={"opportunity_id": evaluation_opportunity_id})
            if evaluation_opportunity_id
            else request
        ),
        state or provider_state(),
        risk_score=Decimal("0.2"),
        liquidity_score=Decimal("0.8"),
        capital_lock_up=timedelta(hours=1),
    )


def test_orchestrator_ranks_and_rejects_sandbox_candidates() -> None:
    adapters = (
        SandboxEngineAdapter(
            candidate("alpha", EngineId.ALPHA, "40", "6", sandbox=True)
        ),
        SandboxEngineAdapter(
            candidate("risk", EngineId.YIELD, "10", "9", risk="0.9", sandbox=True)
        ),
    )

    result = LiquidityChecker().allocate(snapshot(), config(), adapters)

    assert result.allocations[0].candidate.id == "alpha"
    assert result.rejections[0].reason is RejectionReason.RISK_LIMIT


def test_verified_bonus_and_sports_candidates_reach_the_existing_allocator() -> None:
    result = LiquidityChecker().allocate(
        snapshot(), config(), (bonus_adapter(), sports_adapter())
    )

    assert {allocation.candidate.engine for allocation in result.allocations} == {
        EngineId.BONUS,
        EngineId.SPORTS_CAPITAL,
    }
    assert result.rejections == ()


def test_provider_frequency_rejection_prevents_bonus_allocation() -> None:
    result = LiquidityChecker().allocate(
        snapshot(),
        config(),
        (bonus_adapter(state=provider_state(active_bets_count=2)),),
    )

    assert result.allocations == ()
    assert result.rejections[0].reason is RejectionReason.PROVIDER_FREQUENCY_LIMIT


def test_provider_cooldown_rejection_prevents_sports_allocation() -> None:
    result = LiquidityChecker().allocate(
        snapshot(),
        config(),
        (sports_adapter(state=provider_state(is_cooldown_active=True)),),
    )

    assert result.allocations == ()
    assert result.rejections[0].reason is RejectionReason.PROVIDER_COOLDOWN_ACTIVE


def test_existing_allocator_limits_still_apply_after_verification() -> None:
    result = LiquidityChecker().allocate(
        snapshot(), config(), (bonus_adapter(risk="0.9"),)
    )

    assert result.allocations == ()
    assert result.rejections[0].reason is RejectionReason.RISK_LIMIT


def test_verified_strategy_adapter_rejects_a_different_bonus_opportunity() -> None:
    with pytest.raises(ValueError, match="same opportunity"):
        bonus_adapter(evaluation_opportunity_id="different-bonus-opportunity")


def test_verified_strategy_adapter_rejects_a_different_sports_opportunity() -> None:
    with pytest.raises(ValueError, match="same opportunity"):
        sports_adapter(evaluation_opportunity_id="different-sports-opportunity")


def test_recheck_result_prevents_capital_allocation() -> None:
    from qbet.domain.verification import (
        DomainRiskDecisionCode,
        DomainRiskStatus,
        VerificationResult,
    )

    candidate = (
        bonus_adapter()
        .evaluate_candidates(snapshot())[0]
        .model_copy(
            update={
                "verification_result": VerificationResult(
                    is_allowed=False,
                    status=DomainRiskStatus.RECHECK,
                    decision_code=DomainRiskDecisionCode.RECHECK_REQUIRED,
                )
            }
        )
    )

    class RecheckAdapter:
        def evaluate_candidates(
            self, snapshot: CapitalSnapshot
        ) -> tuple[EngineCandidate, ...]:
            return (candidate,)

    result = LiquidityChecker().allocate(snapshot(), config(), (RecheckAdapter(),))
    assert result.allocations == ()
    assert result.rejections[0].reason is RejectionReason.PROVIDER_RECHECK_REQUIRED


def test_concrete_liquidity_checker_allows_eligible_workflow_candidate() -> None:
    checker = LiquidityChecker(
        snapshot(),
        config(),
        (
            SandboxEngineAdapter(
                candidate("eligible", EngineId.ALPHA, "40", "6", sandbox=True)
            ),
        ),
    )

    result = WorkflowOrchestrator(liquidity_checker=checker).process(
        WorkflowRequest(
            id="workflow-liquidity-allow",
            mode=WorkflowMode.SIMULATION,
            stages=(WorkflowStage.LIQUIDITY_CHECK,),
        )
    )

    assert result.final_decision is WorkflowDecision.ALLOW
    assert checker.last_result is not None
    assert checker.last_result.allocations[0].candidate.id == "eligible"


def test_concrete_liquidity_checker_rechecks_and_blocks_workflow() -> None:
    from qbet.domain.verification import (
        DomainRiskDecisionCode,
        DomainRiskStatus,
        VerificationResult,
    )

    recheck_candidate = candidate(
        "recheck", EngineId.ALPHA, "40", "6", sandbox=True
    ).model_copy(
        update={
            "verification_result": VerificationResult(
                is_allowed=False,
                status=DomainRiskStatus.RECHECK,
                decision_code=DomainRiskDecisionCode.RECHECK_REQUIRED,
            )
        }
    )
    checker = LiquidityChecker(
        snapshot(), config(), (SandboxEngineAdapter(recheck_candidate),)
    )

    result = WorkflowOrchestrator(liquidity_checker=checker).process(
        WorkflowRequest(
            id="workflow-liquidity-recheck",
            mode=WorkflowMode.SIMULATION,
            stages=(WorkflowStage.LIQUIDITY_CHECK,),
        )
    )

    assert result.final_decision is WorkflowDecision.RECHECK
    assert checker.last_result is not None
    assert checker.last_result.allocations == ()
    assert (
        checker.last_result.rejections[0].reason
        is RejectionReason.PROVIDER_RECHECK_REQUIRED
    )


def test_liquidity_checker_rejects_currency_liquidity_and_capital_limits() -> None:
    result = LiquidityChecker().allocate(
        snapshot(),
        config(),
        (
            SandboxEngineAdapter(
                candidate(
                    "currency", EngineId.ALPHA, "10", "5", sandbox=True, currency="GBP"
                )
            ),
            SandboxEngineAdapter(
                candidate(
                    "liquidity",
                    EngineId.ALPHA,
                    "10",
                    "4",
                    sandbox=True,
                    liquidity="0.1",
                )
            ),
            SandboxEngineAdapter(
                candidate("capital", EngineId.ALPHA, "1001", "3", sandbox=True)
            ),
        ),
    )
    assert result.allocations == ()
    assert [rejection.reason for rejection in result.rejections] == [
        RejectionReason.CURRENCY_MISMATCH,
        RejectionReason.LIQUIDITY_LIMIT,
        RejectionReason.CAPITAL_LIMIT,
    ]


def test_execution_workflow_rejects_sandbox_before_dispatch() -> None:
    checker = LiquidityChecker(
        snapshot(),
        config(),
        (
            SandboxEngineAdapter(
                candidate("sandbox", EngineId.ALPHA, "10", "5", sandbox=True)
            ),
        ),
    )
    result = WorkflowOrchestrator(liquidity_checker=checker).process(
        WorkflowRequest(
            id="execution-sandbox",
            mode=WorkflowMode.EXECUTION,
            stages=(WorkflowStage.LIQUIDITY_CHECK, WorkflowStage.DISPATCH),
        )
    )
    assert result.final_decision is WorkflowDecision.REJECT
    assert [transition.stage for transition in result.transitions] == [
        WorkflowStage.LIQUIDITY_CHECK
    ]
    assert checker.last_result is not None
    assert (
        checker.last_result.rejections[0].reason
        is RejectionReason.SANDBOX_EXECUTION_PROHIBITED
    )


def test_liquidity_checker_uses_full_deterministic_tie_break_order() -> None:
    result = LiquidityChecker().allocate(
        CapitalSnapshot(available_capital=Decimal(10000), currency="EUR"),
        config(),
        (
            SandboxEngineAdapter(
                candidate(
                    "id-b",
                    EngineId.ALPHA,
                    "20",
                    "10",
                    sandbox=True,
                    risk="0.2",
                    liquidity="0.8",
                    lock_up=timedelta(hours=2),
                )
            ),
            SandboxEngineAdapter(
                candidate(
                    "lock",
                    EngineId.ALPHA,
                    "20",
                    "10",
                    sandbox=True,
                    risk="0.2",
                    liquidity="0.8",
                    lock_up=timedelta(hours=1),
                )
            ),
            SandboxEngineAdapter(
                candidate(
                    "liquidity",
                    EngineId.ALPHA,
                    "20",
                    "10",
                    sandbox=True,
                    risk="0.2",
                    liquidity="0.9",
                )
            ),
            SandboxEngineAdapter(
                candidate("risk", EngineId.ALPHA, "20", "10", sandbox=True, risk="0.1")
            ),
            SandboxEngineAdapter(
                candidate("roi", EngineId.ALPHA, "10", "10", sandbox=True)
            ),
            SandboxEngineAdapter(
                candidate("ev", EngineId.ALPHA, "10", "20", sandbox=True)
            ),
            SandboxEngineAdapter(
                candidate(
                    "id-a",
                    EngineId.ALPHA,
                    "20",
                    "10",
                    sandbox=True,
                    risk="0.2",
                    liquidity="0.8",
                    lock_up=timedelta(hours=2),
                )
            ),
        ),
    )
    assert [allocation.candidate.id for allocation in result.allocations] == [
        "ev",
        "roi",
        "risk",
        "liquidity",
        "lock",
        "id-a",
        "id-b",
    ]


def test_execution_allocation_skips_sandbox_and_keeps_executable_candidate() -> None:
    checker = LiquidityChecker(
        CapitalSnapshot(available_capital=Decimal(120), currency="EUR"),
        config(),
        (
            SandboxEngineAdapter(
                candidate("sandbox", EngineId.ALPHA, "100", "100", sandbox=True)
            ),
            sports_adapter(),
        ),
    )
    result = WorkflowOrchestrator(liquidity_checker=checker).process(
        WorkflowRequest(
            id="mixed-execution",
            mode=WorkflowMode.EXECUTION,
            stages=(WorkflowStage.LIQUIDITY_CHECK, WorkflowStage.DISPATCH),
        )
    )
    assert result.final_decision is WorkflowDecision.ALLOW
    assert [transition.stage for transition in result.transitions] == [
        WorkflowStage.LIQUIDITY_CHECK,
        WorkflowStage.DISPATCH,
    ]
    assert checker.last_result is not None
    assert [
        allocation.candidate.engine for allocation in checker.last_result.allocations
    ] == [EngineId.SPORTS_CAPITAL]
    assert (
        checker.last_result.rejections[0].reason
        is RejectionReason.SANDBOX_EXECUTION_PROHIBITED
    )
