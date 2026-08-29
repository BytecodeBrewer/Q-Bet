from datetime import datetime, timezone
from decimal import Decimal

from qbet.calculations import QualifyingBetInput, calculate_qualifying_bet
from qbet.domain.models import StrategyResult
from qbet.layers import SimulationLogRecordType
from qbet.reporting import ReportDetailSelection, SimulationReportBuilder
from qbet.simulation import (
    ReportingSimulationRunner,
    SimulationEngine,
    SimulationEvaluation,
    SimulationRunConfig,
    SimulationStatus,
    SimulationStep,
)


def config() -> SimulationRunConfig:
    return SimulationRunConfig(
        engine=SimulationEngine.BONUS,
        starting_capital=Decimal("100"),
        strategy_id="qualifying_bet",
    )


def evaluated_step() -> SimulationStep:
    calculation = calculate_qualifying_bet(
        QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100"),
        )
    )
    worst_case = min(
        calculation.back_win_profit_loss,
        calculation.lay_win_profit_loss,
    )
    evaluation = SimulationEvaluation(
        strategy_result=StrategyResult(
            strategy="qualifying_bet",
            opportunity_id="opportunity-1",
            stake=calculation.back_stake,
            expected_profit=worst_case,
            currency="EUR",
            generated_at=datetime(2026, 8, 29, tzinfo=timezone.utc),
        ),
        calculation_result=calculation,
        worst_case_profit_loss=worst_case,
        is_profitable=False,
    )
    return SimulationStep(
        id="evaluated-step",
        capital_change=worst_case,
        evaluation=evaluation,
    )


def test_default_report_is_compact_but_selected_detail_is_available() -> None:
    runner = ReportingSimulationRunner()

    result = runner.run(config(), (evaluated_step(),))

    assert result.status is SimulationStatus.COMPLETED
    assert runner.last_report is not None
    assert runner.last_report.raw_input_snapshots == ()
    assert runner.last_report.events == ()
    assert runner.last_report.intermediate_results == ()
    assert runner.last_report.completed_steps[0].id == "evaluated-step"
    compact_json = runner.last_report.model_dump_json()
    assert "calculation_result" not in compact_json
    assert "strategy_result" not in compact_json
    assert "back_odds" not in compact_json

    detailed = SimulationReportBuilder().build(
        runner.last_report.run_id,
        result,
        runner.last_records,
        ReportDetailSelection(
            include_events=True,
            include_intermediate_results=True,
            include_raw_inputs=True,
        ),
    )
    assert len(detailed.events) == len(result.events)
    assert detailed.events[0].sequence == 1
    assert detailed.intermediate_results == result.evaluations
    assert detailed.raw_input_snapshots[0]["config"]["starting_capital"] == "100"
    assert [record.sequence for record in runner.last_records] == list(
        range(1, len(runner.last_records) + 1)
    )
    assert [record.record_type for record in runner.last_records] == [
        SimulationLogRecordType.RAW_INPUT,
        SimulationLogRecordType.RUN_STARTED,
        SimulationLogRecordType.EVENT,
        SimulationLogRecordType.CAPITAL_TRANSITION,
        SimulationLogRecordType.INTERMEDIATE_RESULT,
        SimulationLogRecordType.EVALUATION,
        SimulationLogRecordType.EVENT,
        SimulationLogRecordType.RUN_FINISHED,
    ]


def test_stopped_run_keeps_completed_steps_and_a_chronological_warning() -> None:
    runner = ReportingSimulationRunner()
    steps = (
        SimulationStep(id="first", capital_change=Decimal("5")),
        SimulationStep(id="second", capital_change=Decimal("5")),
    )

    result = runner.run(
        config(),
        steps,
        on_step_completed=lambda _: runner.request_stop(),
    )

    assert result.status is SimulationStatus.STOPPED
    assert tuple(step.id for step in result.completed_steps) == ("first",)
    assert runner.last_report is not None
    assert runner.last_report.status is SimulationStatus.STOPPED
    assert [record.record_type for record in runner.last_records] == [
        SimulationLogRecordType.RAW_INPUT,
        SimulationLogRecordType.RUN_STARTED,
        SimulationLogRecordType.EVENT,
        SimulationLogRecordType.CAPITAL_TRANSITION,
        SimulationLogRecordType.INTERMEDIATE_RESULT,
        SimulationLogRecordType.EVENT,
        SimulationLogRecordType.EVENT,
        SimulationLogRecordType.WARNING,
        SimulationLogRecordType.RUN_FINISHED,
    ]
