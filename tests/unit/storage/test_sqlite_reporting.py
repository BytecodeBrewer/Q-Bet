from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from qbet.calculations import QualifyingBetInput, calculate_qualifying_bet
from qbet.domain.models import StrategyResult
from qbet.layers import SimulationLogRecord, SimulationLogRecordType
from qbet.reporting import ReportDetailSelection, SimulationReportBuilder
from qbet.simulation import (
    ReportingSimulationRunner,
    SimulationEngine,
    SimulationEvaluation,
    SimulationRunConfig,
    SimulationStep,
    SimulationTopUpEvent,
)
from qbet.storage import SQLiteSimulationReportStore


def evaluated_step() -> SimulationStep:
    calculation = calculate_qualifying_bet(
        QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal(10),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal(100),
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
            generated_at=datetime(2026, 8, 29, tzinfo=UTC),
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


def test_sqlite_round_trip_preserves_top_up_timeline_and_net_profit(tmp_path) -> None:
    database_path = tmp_path / "top-ups.sqlite3"
    store = SQLiteSimulationReportStore(database_path)
    runner = ReportingSimulationRunner(store)

    runner.run(
        SimulationRunConfig(
            engine=SimulationEngine.BONUS,
            starting_capital=Decimal(100),
            top_up_events=(SimulationTopUpEvent(after_completed_steps=1, amount=Decimal(10)),),
        ),
        (SimulationStep(id="flat", capital_change=Decimal(0)),),
    )

    assert runner.last_report is not None
    reopened_store = SQLiteSimulationReportStore(database_path)
    report = reopened_store.load_report(runner.last_report.run_id)
    records = reopened_store.load_records(runner.last_report.run_id)
    transition_payloads = [
        record.payload
        for record in records
        if record.record_type is SimulationLogRecordType.CAPITAL_TRANSITION
    ]

    assert report.current_capital == Decimal(110)
    assert report.top_up_total == Decimal(10)
    assert report.profit_loss == Decimal(0)
    assert transition_payloads == [
        {
            "movement_type": "step",
            "step_id": "flat",
            "capital_change": "0",
            "current_capital": "100",
        },
        {
            "movement_type": "top_up",
            "capital_change": "10",
            "current_capital": "110",
        },
    ]


def test_sqlite_rebuild_restores_selected_risk_decisions(tmp_path) -> None:
    database_path = tmp_path / "risk-decisions.sqlite3"
    store = SQLiteSimulationReportStore(database_path)
    runner = ReportingSimulationRunner(store)
    runner.run(
        SimulationRunConfig(engine=SimulationEngine.BONUS, starting_capital=Decimal(100)),
        (SimulationStep(id="flat", capital_change=Decimal(0)),),
    )

    assert runner.last_report is not None
    run_id = runner.last_report.run_id
    persisted_records = store.load_records(run_id)
    store.append_records(
        (
            SimulationLogRecord(
                run_id=run_id,
                sequence=len(persisted_records) + 1,
                timestamp=datetime.now(UTC),
                record_type=SimulationLogRecordType.RISK_DECISION,
                source="layers.operational_risk",
                payload={
                    "status": "warn",
                    "decision_code": "provider_active_bet_limit_approaching",
                    "warning_codes": ["provider_active_bet_limit_approaching"],
                },
            ),
        )
    )

    reopened_store = SQLiteSimulationReportStore(database_path)
    compact_report = reopened_store.load_report(run_id)
    detailed_report = SimulationReportBuilder().rebuild(
        compact_report,
        reopened_store.load_records(run_id),
        ReportDetailSelection(include_risk_decisions=True),
    )

    assert compact_report.risk_decisions == ()
    decision = detailed_report.risk_decisions[0]
    assert decision.run_id == run_id
    assert decision.payload == {
        "status": "warn",
        "decision_code": "provider_active_bet_limit_approaching",
        "warning_codes": ["provider_active_bet_limit_approaching"],
    }
