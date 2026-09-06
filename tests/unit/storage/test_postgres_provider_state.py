from datetime import datetime, timedelta, timezone

from django.test import TestCase

from qbet.domain.verification import ProviderState
from qbet.storage.postgres import PostgresProviderStateRepository


class PostgresProviderStateRepositoryTests(TestCase):
    def test_repository_round_trips_and_upserts(self) -> None:
        timestamp = datetime(2026, 8, 30, 18, 0, tzinfo=timezone(timedelta(hours=2)))
        store = PostgresProviderStateRepository()
        initial = ProviderState(
            provider_id="book",
            active_bets_count=1,
            last_bet_timestamp=timestamp,
            is_cooldown_active=False,
        )

        store.upsert(initial)
        reopened_store = PostgresProviderStateRepository()

        self.assertEqual(reopened_store.get("book"), initial)

        updated = initial.model_copy(update={"active_bets_count": 2, "is_cooldown_active": True})
        reopened_store.upsert(updated)

        self.assertEqual(PostgresProviderStateRepository().get("book"), updated)

    def test_repository_returns_none_for_unknown_provider(self) -> None:
        store = PostgresProviderStateRepository()

        self.assertIsNone(store.get("unknown-book"))
