"""SQLite persistence adapter for structured simulation records and reports."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from qbet.layers.logging import SimulationLogRecord
from qbet.reporting import SimulationReport


class SQLiteSimulationReportStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS simulation_records ("
                "run_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL, "
                "PRIMARY KEY (run_id, sequence))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS simulation_reports ("
                "run_id TEXT PRIMARY KEY, generated_at TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    def append_records(self, records: tuple[SimulationLogRecord, ...]) -> None:
        if not records:
            return
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO simulation_records (run_id, sequence, payload) VALUES (?, ?, ?)",
                [
                    (str(record.run_id), record.sequence, record.model_dump_json())
                    for record in records
                ],
            )

    def finalize_run(self, report: SimulationReport) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO simulation_reports (run_id, generated_at, payload) "
                "VALUES (?, ?, ?)",
                (
                    str(report.run_id),
                    report.generated_at.isoformat(),
                    report.model_dump_json(),
                ),
            )

    def load_report(self, run_id: UUID) -> SimulationReport:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM simulation_reports WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        if row is None:
            raise KeyError(f"simulation report {run_id} was not found")
        return SimulationReport.model_validate_json(row[0])

    def load_records(self, run_id: UUID) -> tuple[SimulationLogRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM simulation_records WHERE run_id = ? ORDER BY sequence ASC",
                (str(run_id),),
            ).fetchall()
        if not rows:
            raise KeyError(f"simulation records for {run_id} were not found")
        return tuple(SimulationLogRecord.model_validate_json(row[0]) for row in rows)

    def list_recent_reports(self, limit: int = 20) -> tuple[SimulationReport, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM simulation_reports ORDER BY generated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(SimulationReport.model_validate_json(row[0]) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._database_path)
