from datetime import UTC, datetime
from decimal import Decimal

from qbet.calculations import (
    ArbitrageOffer,
    DutchingInput,
    DutchingOffer,
    DutchingResult,
    DutchingTargetMode,
    FreeBetInput,
    FreeBetResult,
    FreeBetStakeReturn,
    QualifyingBetInput,
    QualifyingBetResult,
    TwoWayArbitrageInput,
    TwoWayArbitrageResult,
)
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.simulation import (
    BonusSimulationAdapter,
    DeterministicSimulationRunner,
    SimulationEngine,
    SimulationRunConfig,
    SimulationStatus,
    SportsCapitalSimulationAdapter,
)

GENERATED_AT = datetime(2026, 8, 29, tzinfo=UTC)


def config(engine: SimulationEngine, **changes: object) -> SimulationRunConfig:
    values: dict[str, object] = {
        "engine": engine,
        "starting_capital": Decimal(100),
    }
    values.update(changes)
    return SimulationRunConfig.model_validate(values)


def bonus_requests() -> tuple[BonusEngineRequest, ...]:
    return (
        BonusEngineRequest(
            opportunity_id="qualifying-opportunity",
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
        ),
        BonusEngineRequest(
            opportunity_id="free-bet-opportunity",
            inputs=FreeBetInput(
                free_bet_amount=Decimal(10),
                back_odds=Decimal(5),
                lay_odds=Decimal("5.2"),
                exchange_commission=Decimal("0.02"),
                stake_precision=Decimal("0.01"),
                stake_return_rule=FreeBetStakeReturn.STAKE_NOT_RETURNED,
            ),
            currency="EUR",
            execution_offer_ids=("book", "exchange"),
            generated_at=GENERATED_AT,
        ),
    )


def sports_requests() -> tuple[SportsCapitalEngineRequest, ...]:
    def arbitrage_offer(outcome: str) -> ArbitrageOffer:
        return ArbitrageOffer(
            outcome=outcome,
            odds=Decimal("2.2"),
            available_liquidity=Decimal(100),
            stake_precision=Decimal("0.01"),
            currency="EUR",
        )

    def dutching_offer(outcome: str) -> DutchingOffer:
        return DutchingOffer(
            outcome=outcome,
            odds=Decimal("3.5"),
            available_liquidity=Decimal(100),
            stake_precision=Decimal("0.01"),
            currency="EUR",
        )

    return (
        SportsCapitalEngineRequest(
            opportunity_id="arbitrage-opportunity",
            inputs=TwoWayArbitrageInput(
                first_offer=arbitrage_offer("home"),
                second_offer=arbitrage_offer("away"),
                requested_total_stake=Decimal(20),
            ),
            currency="EUR",
            execution_offer_ids=("home", "away"),
            generated_at=GENERATED_AT,
        ),
        SportsCapitalEngineRequest(
            opportunity_id="dutching-opportunity",
            inputs=DutchingInput(
                offers=(
                    dutching_offer("home"),
                    dutching_offer("draw"),
                    dutching_offer("away"),
                ),
                target_mode=DutchingTargetMode.TOTAL_STAKE,
                total_stake=Decimal(20),
                outcomes_are_exhaustive=True,
            ),
            currency="EUR",
            execution_offer_ids=("home", "draw", "away"),
            generated_at=GENERATED_AT,
        ),
    )


def test_bonus_adapter_evaluates_qualifying_and_free_bet_requests() -> None:
    simulation_config = config(SimulationEngine.BONUS)
    steps = BonusSimulationAdapter(bonus_requests()).build_steps(simulation_config)

    result = DeterministicSimulationRunner().run(simulation_config, steps)

    assert [evaluation.strategy_result.strategy for evaluation in result.evaluations] == [
        "qualifying_bet",
        "free_bet",
    ]
    assert [type(evaluation.calculation_result) for evaluation in result.evaluations] == [
        QualifyingBetResult,
        FreeBetResult,
    ]
    assert result.current_capital == simulation_config.starting_capital + sum(
        (evaluation.worst_case_profit_loss for evaluation in result.evaluations),
        Decimal(0),
    )


def test_sports_adapter_evaluates_arbitrage_and_dutching_requests() -> None:
    simulation_config = config(SimulationEngine.SPORTS_CAPITAL)
    steps = SportsCapitalSimulationAdapter(sports_requests()).build_steps(simulation_config)

    result = DeterministicSimulationRunner().run(simulation_config, steps)

    assert [evaluation.strategy_result.strategy for evaluation in result.evaluations] == [
        "two_way_arbitrage",
        "dutching",
    ]
    assert [type(evaluation.calculation_result) for evaluation in result.evaluations] == [
        TwoWayArbitrageResult,
        DutchingResult,
    ]
    assert result.current_capital == simulation_config.starting_capital + sum(
        (evaluation.worst_case_profit_loss for evaluation in result.evaluations),
        Decimal(0),
    )


def test_engine_simulation_is_reproducible_for_identical_requests() -> None:
    simulation_config = config(SimulationEngine.BONUS)
    adapter = BonusSimulationAdapter(bonus_requests())

    first = DeterministicSimulationRunner().run(
        simulation_config, adapter.build_steps(simulation_config)
    )
    second = DeterministicSimulationRunner().run(
        simulation_config, adapter.build_steps(simulation_config)
    )

    assert first == second


def test_stop_at_safe_boundary_preserves_completed_engine_evaluation() -> None:
    simulation_config = config(SimulationEngine.SPORTS_CAPITAL)
    steps = SportsCapitalSimulationAdapter(sports_requests()).build_steps(simulation_config)
    runner = DeterministicSimulationRunner()

    result = runner.run(simulation_config, steps, on_step_completed=lambda _: runner.request_stop())

    assert result.status is SimulationStatus.STOPPED
    assert len(result.completed_steps) == 1
    assert len(result.evaluations) == 1
    assert (
        result.current_capital
        == simulation_config.starting_capital + result.evaluations[0].worst_case_profit_loss
    )
