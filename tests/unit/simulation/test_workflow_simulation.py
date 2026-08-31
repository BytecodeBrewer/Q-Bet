from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from qbet.calculations import ArbitrageOffer, QualifyingBetInput, TwoWayArbitrageInput
from qbet.domain.verification import ProviderState
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.layers import SimulationLogRecordType
from qbet.reporting import ReportDetailSelection, SimulationReportBuilder
from qbet.simulation import (
    SimulationEngine,
    SimulationRunConfig,
    SimulationTopUpEvent,
    WorkflowSimulationRequest,
    WorkflowSimulationRunner,
)
from qbet.workflow import (
    StaticLiquidityChecker,
    WorkflowDecision,
    WorkflowStage,
    WorkflowStageDecision,
)

GENERATED_AT = datetime(2026, 8, 30, tzinfo=UTC)


class RecordingLiquidityChecker:
    def __init__(self, decision: WorkflowStageDecision) -> None:
        self._decision = decision
        self.contexts = []

    def check(self, context):
        self.contexts.append(context)
        return self._decision


def bonus_request(identifier: str = "bonus-1") -> BonusEngineRequest:
    return BonusEngineRequest(
        opportunity_id=identifier,
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
        generated_at=GENERATED_AT,
    )


def sports_request() -> SportsCapitalEngineRequest:
    def offer(outcome: str) -> ArbitrageOffer:
        return ArbitrageOffer(
            outcome=outcome,
            odds=Decimal("2.2"),
            available_liquidity=Decimal(100),
            stake_precision=Decimal("0.01"),
            currency="EUR",
        )

    return SportsCapitalEngineRequest(
        opportunity_id="sports-1",
        inputs=TwoWayArbitrageInput(
            first_offer=offer("home"),
            second_offer=offer("away"),
            requested_total_stake=Decimal(20),
        ),
        currency="EUR",
        execution_offer_ids=("home", "away"),
        generated_at=GENERATED_AT,
    )


def request(
    engine: SimulationEngine,
    opportunities: tuple[BonusEngineRequest | SportsCapitalEngineRequest, ...],
    **changes: object,
) -> WorkflowSimulationRequest:
    values: dict[str, object] = {
        "config": SimulationRunConfig(engine=engine, starting_capital=Decimal(100)),
        "opportunities": opportunities,
        "provider_state": ProviderState(provider_id="book", active_bets_count=0),
    }
    values.update(changes)
    return WorkflowSimulationRequest(**values)


def test_bonus_simulation_routes_allowed_step_through_required_liquidity_gate() -> None:
    correlation_id = UUID("12345678-1234-5678-1234-567812345678")
    checker = RecordingLiquidityChecker(
        WorkflowStageDecision(decision=WorkflowDecision.ALLOW)
    )
    runner = WorkflowSimulationRunner(liquidity_checker=checker)

    result = runner.run(
        request(
            SimulationEngine.BONUS, (bonus_request(),), correlation_id=correlation_id
        )
    )

    assert result.correlation_id == correlation_id
    assert len(checker.contexts) == 1
    assert len(result.simulation_result.completed_steps) == 1
    assert result.simulation_result.current_capital != Decimal(100)
    assert [
        transition.stage for transition in result.workflow_results[0].transitions
    ] == [
        WorkflowStage.DATA_AGGREGATION,
        WorkflowStage.ENGINE_PREPARATION,
        WorkflowStage.CALCULATION,
        WorkflowStage.DOMAIN_RISK,
        WorkflowStage.LIQUIDITY_CHECK,
        WorkflowStage.DISPATCH,
    ]
    liquidity_transition = next(
        record
        for record in runner.last_records
        if record.record_type is SimulationLogRecordType.WORKFLOW_TRANSITION
        and record.payload["stage"] == WorkflowStage.LIQUIDITY_CHECK.value
    )
    capital_transition = next(
        record
        for record in runner.last_records
        if record.record_type is SimulationLogRecordType.CAPITAL_TRANSITION
    )
    assert liquidity_transition.sequence < capital_transition.sequence
    assert {record.run_id for record in runner.last_records} == {correlation_id}


def test_sports_capital_simulation_uses_concrete_engine() -> None:
    result = WorkflowSimulationRunner().run(
        request(SimulationEngine.SPORTS_CAPITAL, (sports_request(),))
    )

    assert (
        result.simulation_result.evaluations[0].strategy_result.strategy
        == "two_way_arbitrage"
    )
    assert result.workflow_results[0].mode.value == "simulation"


def test_risk_rejection_stops_before_initial_top_up_or_virtual_capital_change() -> None:
    runner = WorkflowSimulationRunner()
    result = runner.run(
        request(
            SimulationEngine.BONUS,
            (bonus_request(),),
            config=SimulationRunConfig(
                engine=SimulationEngine.BONUS,
                starting_capital=Decimal(100),
                top_up_events=(
                    SimulationTopUpEvent(after_completed_steps=0, amount=Decimal(10)),
                ),
            ),
            provider_state=ProviderState(provider_id="book", active_bets_count=2),
        )
    )

    assert result.simulation_result.completed_steps == ()
    assert result.simulation_result.current_capital == Decimal(100)
    assert result.workflow_results[0].final_decision is WorkflowDecision.REJECT
    assert all(
        event.event_type.value != "top_up_applied"
        for event in result.simulation_result.events
    )
    assert runner.last_report is not None
    detailed = SimulationReportBuilder().rebuild(
        runner.last_report,
        runner.last_records,
        ReportDetailSelection(include_risk_decisions=True),
    )
    assert detailed.risk_decisions[0].payload["status"] == "reject"


def test_liquidity_recheck_stops_before_initial_top_up_and_is_reportable() -> None:
    runner = WorkflowSimulationRunner(
        liquidity_checker=StaticLiquidityChecker(
            WorkflowStageDecision(
                decision=WorkflowDecision.RECHECK,
                reason="refresh balance",
            )
        )
    )
    result = runner.run(
        request(
            SimulationEngine.BONUS,
            (bonus_request(),),
            config=SimulationRunConfig(
                engine=SimulationEngine.BONUS,
                starting_capital=Decimal(100),
                top_up_events=(
                    SimulationTopUpEvent(after_completed_steps=0, amount=Decimal(10)),
                ),
            ),
        )
    )

    assert result.simulation_result.completed_steps == ()
    assert result.simulation_result.current_capital == Decimal(100)
    assert result.workflow_results[0].final_decision is WorkflowDecision.RECHECK
    assert runner.last_report is not None
    detailed = SimulationReportBuilder().rebuild(
        runner.last_report,
        runner.last_records,
        ReportDetailSelection(include_workflow_transitions=True),
    )
    liquidity_transition = next(
        record
        for record in detailed.workflow_transitions
        if record.payload["stage"] == WorkflowStage.LIQUIDITY_CHECK.value
    )
    assert liquidity_transition.payload["decision"] == WorkflowDecision.RECHECK.value
    assert liquidity_transition.payload["reason"] == "refresh balance"


def test_stop_request_preserves_first_completed_workflow_step() -> None:
    runner = WorkflowSimulationRunner()
    result = runner.run(
        request(
            SimulationEngine.BONUS,
            (bonus_request("first"), bonus_request("second")),
        ),
        on_step_completed=lambda _: runner.request_stop(),
    )

    assert len(result.simulation_result.completed_steps) == 1
    assert result.simulation_result.current_capital != Decimal(100)
    assert len(result.workflow_results) == 1


def test_liquidity_rejection_stops_before_virtual_capital_change() -> None:
    runner = WorkflowSimulationRunner(
        liquidity_checker=StaticLiquidityChecker(
            WorkflowStageDecision(
                decision=WorkflowDecision.REJECT,
                reason="insufficient virtual liquidity",
            )
        )
    )
    result = runner.run(request(SimulationEngine.BONUS, (bonus_request(),)))

    assert result.simulation_result.completed_steps == ()
    assert result.simulation_result.current_capital == Decimal(100)
    assert result.workflow_results[0].final_decision is WorkflowDecision.REJECT
