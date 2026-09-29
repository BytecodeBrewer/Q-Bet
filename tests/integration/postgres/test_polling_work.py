from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.test import TestCase

from qbet.data.models import DataSourceMetadata, SourceTransport
from qbet.data.polling import PollingTarget
from qbet.data.polling_runtime import PollingWork
from qbet.storage.models import PollingWorkRow
from qbet.storage.polling_work import PostgresPollingWorkRepository


NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
SOURCE = DataSourceMetadata(
    provider_id="the_odds_api",
    source_id="simulation-the-odds-api",
    transport=SourceTransport.API,
)


def work(*, owner: str = "alice", match_id: str = "event-1", **changes: object) -> PollingWork:
    values: dict[str, object] = {
        "owner": owner,
        "source": SOURCE,
        "target": PollingTarget.MARKET,
        "engine": "sports_capital",
        "mode": "simulation",
        "match_id": match_id,
        "correlation_id": UUID("12345678-1234-5678-1234-567812345678"),
        "sport": "soccer_germany_bundesliga",
        "market": "h2h",
        "event_starts_at": NOW + timedelta(hours=2),
        "next_due_at": NOW,
    }
    values.update(changes)
    return PollingWork.model_validate(values)


class PollingWorkRepositoryTests(TestCase):
    def test_work_survives_repository_recreation_and_duplicate_sync_is_idempotent(self) -> None:
        repository = PostgresPollingWorkRepository()
        repository.synchronize((work(), work()))

        self.assertEqual(PollingWorkRow.objects.count(), 1)
        self.assertEqual(PostgresPollingWorkRepository().list(), (work(),))

    def test_claim_is_bounded_and_lease_prevents_duplicate_concurrent_claim(self) -> None:
        repository = PostgresPollingWorkRepository()
        first = work(owner="alice", match_id="event-a")
        second = work(
            owner="bob",
            match_id="event-b",
            correlation_id=UUID("22345678-1234-5678-1234-567812345678"),
        )
        repository.synchronize((first, second))

        first_claim = repository.claim_due(
            now=NOW,
            limit=1,
            lease_for=timedelta(minutes=2),
        )
        second_claim = repository.claim_due(
            now=NOW,
            limit=1,
            lease_for=timedelta(minutes=2),
        )

        self.assertEqual(len(first_claim), 1)
        self.assertEqual(len(second_claim), 1)
        self.assertNotEqual(first_claim[0].identity, second_claim[0].identity)

    def test_route_removal_disables_future_work_without_deleting_history(self) -> None:
        repository = PostgresPollingWorkRepository()
        repository.synchronize((work(),))

        repository.synchronize(())

        stored = repository.list()
        self.assertEqual(len(stored), 1)
        self.assertTrue(stored[0].disabled)
        self.assertEqual(
            repository.claim_due(
                now=NOW,
                limit=10,
                lease_for=timedelta(minutes=2),
            ),
            (),
        )
