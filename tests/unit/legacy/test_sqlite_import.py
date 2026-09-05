from __future__ import annotations

import io
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.core.management import call_command
from django.test import TestCase

from qbet.domain.verification import ProviderState
from qbet.layers.logging import SimulationLogRecord, SimulationLogRecordType
from qbet.legacy.sqlite_import import import_provider_state, import_simulation_history
from qbet.reporting import SimulationReport
from qbet.simulation import SimulationEngine, SimulationRunConfig, SimulationStatus
from qbet.storage.postgres import (
    PostgresProviderStateRepository,
    PostgresSimulationReportStore,
)


class LegacySQLiteImportTests(TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.directory_path = Path(self.directory.name)

    def _simulation_source(self) -> tuple[Path, SimulationReport, SimulationLogRecord]:
        source = self.directory_path / "simulation.sqlite3"
        run_id = uuid4()
        report = SimulationReport(
            run_id=run_id,
            config=SimulationRunConfig(
                engine=SimulationEngine.BONUS,
                starting_capital=Decimal("100"),
            ),
            engine=SimulationEngine.BONUS.value,
            strategy_id=None,
            status=SimulationStatus.COMPLETED,
            starting_capital=Decimal("100"),
            current_capital=Decimal("105"),
            top_up_total=Decimal("0"),
            profit_loss=Decimal("5"),
            completed_steps=(),
            elapsed_duration=timedelta(minutes=5),
            progress=Decimal("1"),
            generated_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        )
        record = SimulationLogRecord(
            run_id=run_id,
            sequence=1,
            timestamp=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
            record_type=SimulationLogRecordType.RUN_FINISHED,
            source="legacy-test",
            payload={"status": "completed"},
        )
        with closing(sqlite3.connect(source)) as connection, connection:
            connection.execute(
                "CREATE TABLE simulation_reports ("
                "run_id TEXT PRIMARY KEY, generated_at TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE simulation_records ("
                "run_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL, "
                "PRIMARY KEY (run_id, sequence))"
            )
            connection.execute(
                "INSERT INTO simulation_reports VALUES (?, ?, ?)",
                (str(run_id), report.generated_at.isoformat(), report.model_dump_json()),
            )
            connection.execute(
                "INSERT INTO simulation_records VALUES (?, ?, ?)",
                (str(run_id), record.sequence, record.model_dump_json()),
            )
        return source, report, record

    def _provider_source(self) -> tuple[Path, ProviderState]:
        source = self.directory_path / "provider.sqlite3"
        state = ProviderState(
            provider_id="legacy-book",
            active_bets_count=1,
            last_bet_timestamp=datetime(2026, 9, 4, 11, 30, tzinfo=UTC),
            is_cooldown_active=False,
        )
        with closing(sqlite3.connect(source)) as connection, connection:
            connection.execute(
                "CREATE TABLE provider_states ("
                "provider_id TEXT PRIMARY KEY, active_bets_count INTEGER NOT NULL, "
                "last_bet_timestamp TEXT, is_cooldown_active INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO provider_states VALUES (?, ?, ?, ?)",
                (
                    state.provider_id,
                    state.active_bets_count,
                    state.last_bet_timestamp.isoformat(),
                    int(state.is_cooldown_active),
                ),
            )
        return source, state

    def test_simulation_import_is_repeatable_and_preserves_payloads(self) -> None:
        source, report, record = self._simulation_source()

        first = import_simulation_history(source)
        second = import_simulation_history(source)
        target = PostgresSimulationReportStore()

        self.assertEqual((first.imported, first.skipped, first.conflicts), (2, 0, 0))
        self.assertEqual((second.imported, second.skipped, second.conflicts), (0, 2, 0))
        self.assertEqual(target.load_report(report.run_id), report)
        self.assertEqual(target.load_records(report.run_id), (record,))

    def test_provider_import_skips_equal_state_and_reports_conflict_without_overwrite(self) -> None:
        source, state = self._provider_source()
        repository = PostgresProviderStateRepository()

        first = import_provider_state(source)
        second = import_provider_state(source)
        repository.upsert(state.model_copy(update={"active_bets_count": 2}))
        conflict = import_provider_state(source)

        self.assertEqual((first.imported, first.skipped, first.conflicts), (1, 0, 0))
        self.assertEqual((second.imported, second.skipped, second.conflicts), (0, 1, 0))
        self.assertEqual((conflict.imported, conflict.skipped, conflict.conflicts), (0, 0, 1))
        persisted = repository.get(state.provider_id)
        assert persisted is not None
        self.assertEqual(persisted.active_bets_count, 2)

    def test_management_command_reports_import_summary(self) -> None:
        simulation_source, _, _ = self._simulation_source()
        provider_source, _ = self._provider_source()
        output = io.StringIO()

        call_command(
            "import_legacy_sqlite",
            simulation_db=str(simulation_source),
            provider_db=str(provider_source),
            stdout=output,
        )

        rendered = output.getvalue()
        self.assertIn("simulation: imported=2 skipped=0 conflicts=0", rendered)
        self.assertIn("provider-state: imported=1 skipped=0 conflicts=0", rendered)
        self.assertIn("total: imported=3 skipped=0 conflicts=0", rendered)
