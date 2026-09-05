from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from qbet.domain.verification import ProviderState
from qbet.layers.logging import SimulationLogRecord
from qbet.reporting import SimulationReport
from qbet.storage.postgres import (
    PostgresProviderStateRepository,
    PostgresSimulationReportStore,
)


class LegacySQLiteImportError(RuntimeError):
    """Raised when a supplied legacy SQLite source cannot be imported safely."""


@dataclass(frozen=True, slots=True)
class ImportSummary:
    imported: int = 0
    skipped: int = 0
    conflicts: int = 0

    def combine(self, other: "ImportSummary") -> "ImportSummary":
        return ImportSummary(
            imported=self.imported + other.imported,
            skipped=self.skipped + other.skipped,
            conflicts=self.conflicts + other.conflicts,
        )


def _connect_read_only(path: str | Path) -> sqlite3.Connection:
    source = Path(path)
    if not source.is_file():
        raise LegacySQLiteImportError(f"Legacy SQLite source does not exist: {source}")
    database_uri = f"file:{source.resolve().as_posix()}?mode=ro"
    try:
        return sqlite3.connect(database_uri, uri=True)
    except sqlite3.Error as error:
        raise LegacySQLiteImportError(f"Could not open legacy SQLite source: {source}") from error


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def import_simulation_history(
    path: str | Path,
    *,
    store: PostgresSimulationReportStore | None = None,
) -> ImportSummary:
    """Import legacy simulation reports/records without overwriting conflicts."""

    target = store or PostgresSimulationReportStore()
    summary = ImportSummary()
    connection = _connect_read_only(path)
    try:
        if not _table_exists(connection, "simulation_reports"):
            raise LegacySQLiteImportError("Legacy source has no simulation_reports table")
        if not _table_exists(connection, "simulation_records"):
            raise LegacySQLiteImportError("Legacy source has no simulation_records table")

        report_rows = connection.execute(
            "SELECT run_id, payload FROM simulation_reports ORDER BY generated_at ASC"
        ).fetchall()
        for raw_run_id, payload in report_rows:
            run_id = UUID(str(raw_run_id))
            incoming = SimulationReport.model_validate_json(payload)
            try:
                existing = target.load_report(run_id)
            except KeyError:
                target.finalize_run(incoming)
                summary = summary.combine(ImportSummary(imported=1))
            else:
                if existing == incoming:
                    summary = summary.combine(ImportSummary(skipped=1))
                else:
                    summary = summary.combine(ImportSummary(conflicts=1))

        record_rows = connection.execute(
            "SELECT run_id, sequence, payload FROM simulation_records "
            "ORDER BY run_id ASC, sequence ASC"
        ).fetchall()
        records_by_run: dict[UUID, list[SimulationLogRecord]] = {}
        for raw_run_id, _sequence, payload in record_rows:
            run_id = UUID(str(raw_run_id))
            records_by_run.setdefault(run_id, []).append(
                SimulationLogRecord.model_validate_json(payload)
            )

        for run_id, incoming_records in records_by_run.items():
            try:
                existing_records = target.load_records(run_id)
            except KeyError:
                existing_records = ()
            existing_by_sequence = {record.sequence: record for record in existing_records}
            missing: list[SimulationLogRecord] = []
            for record in incoming_records:
                existing = existing_by_sequence.get(record.sequence)
                if existing is None:
                    missing.append(record)
                    summary = summary.combine(ImportSummary(imported=1))
                elif existing == record:
                    summary = summary.combine(ImportSummary(skipped=1))
                else:
                    summary = summary.combine(ImportSummary(conflicts=1))
            if missing:
                target.append_records(tuple(missing))
    except (sqlite3.Error, ValueError) as error:
        raise LegacySQLiteImportError("Legacy simulation history is malformed") from error
    finally:
        connection.close()
    return summary


def import_provider_state(
    path: str | Path,
    *,
    repository: PostgresProviderStateRepository | None = None,
) -> ImportSummary:
    """Import legacy provider state without overwriting authoritative conflicts."""

    target = repository or PostgresProviderStateRepository()
    summary = ImportSummary()
    connection = _connect_read_only(path)
    try:
        if not _table_exists(connection, "provider_states"):
            raise LegacySQLiteImportError("Legacy source has no provider_states table")
        rows = connection.execute(
            "SELECT provider_id, active_bets_count, last_bet_timestamp, "
            "is_cooldown_active FROM provider_states ORDER BY provider_id ASC"
        ).fetchall()
        for provider_id, active_bets_count, last_bet_timestamp, is_cooldown_active in rows:
            incoming = ProviderState(
                provider_id=provider_id,
                active_bets_count=active_bets_count,
                last_bet_timestamp=(
                    datetime.fromisoformat(last_bet_timestamp)
                    if last_bet_timestamp is not None
                    else None
                ),
                is_cooldown_active=bool(is_cooldown_active),
            )
            existing = target.get(incoming.provider_id)
            if existing is None:
                target.upsert(incoming)
                summary = summary.combine(ImportSummary(imported=1))
            elif existing == incoming:
                summary = summary.combine(ImportSummary(skipped=1))
            else:
                summary = summary.combine(ImportSummary(conflicts=1))
    except (sqlite3.Error, ValueError) as error:
        raise LegacySQLiteImportError("Legacy provider state is malformed") from error
    finally:
        connection.close()
    return summary
