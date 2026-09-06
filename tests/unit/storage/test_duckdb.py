from qbet.storage import ReplayRecord
from qbet.storage.duckdb import DuckDBReplayStore


def test_duckdb_replay_store_persists_records_in_sequence_order(tmp_path) -> None:
    database_path = tmp_path / "analytics.duckdb"
    store = DuckDBReplayStore(database_path)
    store.append(ReplayRecord(stream_id="run-1", sequence=2, payload='{"value": 2}'))
    store.append(ReplayRecord(stream_id="run-1", sequence=1, payload='{"value": 1}'))

    reopened_store = DuckDBReplayStore(database_path)

    assert reopened_store.load_stream("run-1") == (
        ReplayRecord(stream_id="run-1", sequence=1, payload='{"value": 1}'),
        ReplayRecord(stream_id="run-1", sequence=2, payload='{"value": 2}'),
    )


def test_duckdb_replay_store_keeps_streams_isolated(tmp_path) -> None:
    store = DuckDBReplayStore(tmp_path / "analytics.duckdb")
    store.append(ReplayRecord(stream_id="run-a", sequence=0, payload="a"))
    store.append(ReplayRecord(stream_id="run-b", sequence=0, payload="b"))

    assert store.load_stream("run-a") == (ReplayRecord(stream_id="run-a", sequence=0, payload="a"),)
