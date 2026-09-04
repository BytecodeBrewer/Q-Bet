"""Local DuckDB adapter for analytics replay and backtesting payloads."""

from __future__ import annotations

from pathlib import Path

import duckdb

from .protocol import ReplayRecord


class DuckDBReplayStore:
    """Persist ordered analytical replay streams without owning operational state."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS replay_records ("
                "stream_id VARCHAR NOT NULL, "
                "sequence BIGINT NOT NULL, "
                "payload VARCHAR NOT NULL, "
                "PRIMARY KEY (stream_id, sequence))"
            )

    def append(self, record: ReplayRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO replay_records (stream_id, sequence, payload) VALUES (?, ?, ?)",
                (record.stream_id, record.sequence, record.payload),
            )

    def load_stream(self, stream_id: str) -> tuple[ReplayRecord, ...]:
        if not stream_id.strip():
            raise ValueError("stream_id must not be empty")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT stream_id, sequence, payload FROM replay_records "
                "WHERE stream_id = ? ORDER BY sequence ASC",
                (stream_id,),
            ).fetchall()
        return tuple(
            ReplayRecord(stream_id=row[0], sequence=int(row[1]), payload=row[2]) for row in rows
        )

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(self._database_path)
