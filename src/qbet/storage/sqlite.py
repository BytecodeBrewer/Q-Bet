"""SQLite persistence adapters for simulation records and provider state."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import UUID

from qbet.domain.models import Identifier
from qbet.domain.verification import ProviderState
from qbet.layers.logging import SimulationLogRecord
from qbet.reporting import SimulationReport


class SQLiteSimulationReportStore:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with closing(self._connect()) as connection:
            with connection:
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
        with closing(self._connect()) as connection:
            with connection:
                connection.executemany(
                    "INSERT INTO simulation_records (run_id, sequence, payload) VALUES (?, ?, ?)",
                    [
                        (str(record.run_id), record.sequence, record.model_dump_json())
                        for record in records
                    ],
                )

    def finalize_run(self, report: SimulationReport) -> None:
        with closing(self._connect()) as connection:
            with connection:
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
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM simulation_reports WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"simulation report {run_id} was not found")
        return SimulationReport.model_validate_json(row[0])

    def load_records(self, run_id: UUID) -> tuple[SimulationLogRecord, ...]:
        with closing(self._connect()) as connection:
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
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT payload FROM simulation_reports ORDER BY generated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(SimulationReport.model_validate_json(row[0]) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._database_path)


class SQLiteSimulationReportReader:
    """Read simulation history without creating databases or schema."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)

    def load_report(self, run_id: UUID) -> SimulationReport:
        try:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT payload FROM simulation_reports WHERE run_id = ?",
                    (str(run_id),),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.OperationalError as error:
            raise OSError("simulation history is unavailable") from error
        if row is None:
            raise KeyError(f"simulation report {run_id} was not found")
        return SimulationReport.model_validate_json(row[0])

    def load_records(self, run_id: UUID) -> tuple[SimulationLogRecord, ...]:
        try:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT payload FROM simulation_records WHERE run_id = ? ORDER BY sequence ASC",
                    (str(run_id),),
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.OperationalError as error:
            raise OSError("simulation history is unavailable") from error
        if not rows:
            raise KeyError(f"simulation records for {run_id} were not found")
        return tuple(SimulationLogRecord.model_validate_json(row[0]) for row in rows)

    def list_recent_reports(self, limit: int = 20) -> tuple[SimulationReport, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        try:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT payload FROM simulation_reports ORDER BY generated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.OperationalError as error:
            raise OSError("simulation history is unavailable") from error
        return tuple(SimulationReport.model_validate_json(row[0]) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        database_uri = f"file:{self._database_path.resolve().as_posix()}?mode=ro"
        try:
            return sqlite3.connect(database_uri, uri=True)
        except sqlite3.OperationalError as error:
            raise OSError("simulation history is unavailable") from error


class SQLiteProviderStateRepository:
    """Local provider state adapter; replace through ProviderStateRepository for Supabase."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_states ("
                "provider_id TEXT PRIMARY KEY, active_bets_count INTEGER NOT NULL, "
                "last_bet_timestamp TEXT, is_cooldown_active INTEGER NOT NULL)"
            )

    def get(self, provider_id: Identifier) -> ProviderState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT provider_id, active_bets_count, last_bet_timestamp, "
                "is_cooldown_active FROM provider_states WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
        if row is None:
            return None
        return ProviderState(
            provider_id=row[0],
            active_bets_count=row[1],
            last_bet_timestamp=(
                datetime.fromisoformat(row[2]) if row[2] is not None else None
            ),
            is_cooldown_active=bool(row[3]),
        )

    def upsert(self, state: ProviderState) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO provider_states "
                "(provider_id, active_bets_count, last_bet_timestamp, is_cooldown_active) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(provider_id) DO UPDATE SET "
                "active_bets_count = excluded.active_bets_count, "
                "last_bet_timestamp = excluded.last_bet_timestamp, "
                "is_cooldown_active = excluded.is_cooldown_active",
                (
                    state.provider_id,
                    state.active_bets_count,
                    (
                        state.last_bet_timestamp.isoformat()
                        if state.last_bet_timestamp is not None
                        else None
                    ),
                    int(state.is_cooldown_active),
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._database_path)
