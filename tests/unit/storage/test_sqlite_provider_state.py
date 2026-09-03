from datetime import datetime, timedelta, timezone

from qbet.domain.verification import ProviderState
from qbet.storage import SQLiteProviderStateRepository


def test_sqlite_provider_state_repository_round_trips_and_upserts(tmp_path) -> None:
    database_path = tmp_path / "provider-state.sqlite3"
    timestamp = datetime(2026, 8, 30, 18, 0, tzinfo=timezone(timedelta(hours=2)))
    store = SQLiteProviderStateRepository(database_path)
    initial = ProviderState(
        provider_id="book",
        active_bets_count=1,
        last_bet_timestamp=timestamp,
        is_cooldown_active=False,
    )

    store.upsert(initial)
    reopened_store = SQLiteProviderStateRepository(database_path)

    assert reopened_store.get("book") == initial

    updated = initial.model_copy(update={"active_bets_count": 2, "is_cooldown_active": True})
    reopened_store.upsert(updated)

    assert SQLiteProviderStateRepository(database_path).get("book") == updated


def test_sqlite_provider_state_repository_returns_none_for_unknown_provider(
    tmp_path,
) -> None:
    store = SQLiteProviderStateRepository(tmp_path / "provider-state.sqlite3")

    assert store.get("unknown-book") is None
