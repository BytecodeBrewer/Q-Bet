from decimal import Decimal
from uuid import uuid4

import pytest

from qbet.reporting import ReportDetailSelection, SimulationReportBuilder
from qbet.simulation import (
    ReportingSimulationRunner,
    SimulationEngine,
    SimulationRunConfig,
    SimulationStep,
)
from qbet.storage import SQLiteSimulationReportStore


def test_sqlite_store_round_trips_records_and_rebuilds_selected_detail(tmp_path) -> None:
    database_path = tmp_path / "simulation.sqlite3"
    store = SQLiteSimulationReportStore(database_path)
    runner = ReportingSimulationRunner(store)

    result = runner.run(
        SimulationRunConfig(
            engine=SimulationEngine.BONUS,
            starting_capital=Decimal("100.10"),
        ),
        (SimulationStep(id="gain", capital_change=Decimal("0.25")),),
    )

    assert runner.last_report is not None
    run_id = runner.last_report.run_id
    reopened_store = SQLiteSimulationReportStore(database_path)
    compact_report = reopened_store.load_report(run_id)
    records = reopened_store.load_records(run_id)
    detailed_report = SimulationReportBuilder().rebuild(
        compact_report,
        records,
        ReportDetailSelection(include_events=True, include_raw_inputs=True),
    )

    assert compact_report.current_capital == Decimal("100.35")
    assert compact_report.status == result.status
    assert [record.sequence for record in records] == list(range(1, len(records) + 1))
    assert detailed_report.events[0].sequence == 1
    assert detailed_report.raw_input_snapshots[0]["config"]["starting_capital"] == "100.10"
    assert reopened_store.list_recent_reports() == (compact_report,)


def test_sqlite_store_raises_for_missing_run_id(tmp_path) -> None:
    store = SQLiteSimulationReportStore(tmp_path / "simulation.sqlite3")

    with pytest.raises(KeyError):
        store.load_report(uuid4())

    with pytest.raises(KeyError):
        store.load_records(uuid4())
