from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from qbet.calculations import QualifyingBetInput, calculate_qualifying_bet
from qbet.domain.models import StrategyResult
from qbet.reporting import ReportDetailSelection, SimulationReportBuilder
from qbet.simulation import (
    ReportingSimulationRunner,
    SimulationEngine,
    SimulationEvaluation,
    SimulationRunConfig,
    SimulationStep,
)
from qbet.storage import SQLiteSimulationReportStore


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


def test_sqlite_store_rebuilds_selected_detail_after_reopening(tmp_path) -> None:
    database_path = tmp_path / "simulation.sqlite3"
    store = SQLiteSimulationReportStore(database_path)
    runner = ReportingSimulationRunner(store)

    result = runner.run(
        SimulationRunConfig(
            engine=SimulationEngine.BONUS,
            starting_capital=Decimal("100.10"),
        ),
        (evaluated_step(),),
    )

    assert runner.last_report is not None
    run_id = runner.last_report.run_id
    reopened_store = SQLiteSimulationReportStore(database_path)
    compact_report = reopened_store.load_report(run_id)
    records = reopened_store.load_records(run_id)
    detailed_report = SimulationReportBuilder().rebuild(
        compact_report,
        records,
        ReportDetailSelection(
            include_events=True,
            include_intermediate_results=True,
            include_raw_inputs=True,
        ),
    )

    compact_json = compact_report.model_dump_json()
    assert "calculation_result" not in compact_json
    assert "strategy_result" not in compact_json
    assert compact_report.current_capital == result.current_capital
    assert compact_report.status == result.status
    assert [record.sequence for record in records] == list(range(1, len(records) + 1))
    assert detailed_report.events[0].sequence == 1
    assert detailed_report.intermediate_results == result.evaluations
    assert detailed_report.raw_input_snapshots[0]["config"]["starting_capital"] == "100.10"
    assert reopened_store.list_recent_reports() == (compact_report,)


def test_sqlite_store_raises_for_missing_run_id(tmp_path) -> None:
    store = SQLiteSimulationReportStore(tmp_path / "simulation.sqlite3")

    with pytest.raises(KeyError):
        store.load_report(uuid4())

    with pytest.raises(KeyError):
        store.load_records(uuid4())
