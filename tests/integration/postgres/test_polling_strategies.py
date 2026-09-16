from datetime import UTC, datetime, timedelta

from django.test import TestCase

from qbet.data.models import DataSourceMetadata, SourceTransport
from qbet.data.polling import (
    PollingCapacityClass,
    PollingOutcome,
    PollingStrategy,
    PollingTarget,
    SmartPollingPolicy,
)
from qbet.storage.models import PollingStrategyRow
from qbet.storage.polling import PollingStrategyPersistenceError, PollingStrategyRepository


SOURCE = DataSourceMetadata(
    provider_id="provider-a",
    source_id="odds",
    transport=SourceTransport.API,
)


def market_strategy(**changes: object) -> PollingStrategy:
    values: dict[str, object] = {
        "source": SOURCE,
        "target": PollingTarget.MARKET,
        "engine": None,
        "enabled": True,
        "freshness_window": timedelta(minutes=5),
        "market_refresh_points": (
            timedelta(hours=24),
            timedelta(hours=12),
            timedelta(hours=1),
        ),
        "market_interval": None,
        "latest_market_poll_before_event": timedelta(minutes=1),
        "result_retry_interval": timedelta(minutes=10),
        "max_attempts": 3,
        "capacity_class": PollingCapacityClass.FREE,
        "capacity_units": 100,
        "request_cost_units": 1,
    }
    values.update(changes)
    return PollingStrategy.model_validate(values)


class PollingStrategyRepositoryTests(TestCase):
    def test_save_and_reload_survive_repository_recreation(self) -> None:
        saved = PollingStrategyRepository().save(market_strategy())

        reloaded = PollingStrategyRepository().load(
            provider_id="provider-a",
            source_id="odds",
            target=PollingTarget.MARKET,
            engine=None,
        )

        self.assertEqual(reloaded, saved)
        self.assertEqual(PollingStrategyRow.objects.count(), 1)

    def test_same_identity_updates_instead_of_creating_ambiguous_strategy(self) -> None:
        repository = PollingStrategyRepository()
        repository.save(market_strategy())
        updated = repository.save(market_strategy(capacity_units=25, enabled=False))

        self.assertEqual(PollingStrategyRow.objects.count(), 1)
        self.assertEqual(repository.list(), (updated,))

    def test_engine_override_and_default_persist_as_separate_identities(self) -> None:
        repository = PollingStrategyRepository()
        default = repository.save(market_strategy())
        override = repository.save(
            market_strategy(engine="bonus", capacity_class=PollingCapacityClass.PAID)
        )

        resolver = PollingStrategyRepository().resolver()
        default_request = _request(engine="sports_capital")
        override_request = _request(engine="bonus")

        self.assertEqual(resolver.resolve(default_request), default)
        self.assertEqual(resolver.resolve(override_request), override)

    def test_persisted_strategy_drives_policy_after_repository_recreation(self) -> None:
        repository = PollingStrategyRepository()
        repository.save(
            market_strategy(
                market_refresh_points=(timedelta(hours=24), timedelta(hours=1)),
            )
        )

        request = _request(engine="sports_capital")
        event_starts_at = datetime(2026, 9, 17, 18, 0, tzinfo=UTC)
        request = request.model_copy(
            update={
                "event_starts_at": event_starts_at,
                "next_poll_at": datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
                "fetched_at": datetime(2026, 9, 16, 11, 0, tzinfo=UTC),
            }
        )
        policy = SmartPollingPolicy(resolver=PollingStrategyRepository().resolver())

        decision = policy.decide(request)

        self.assertEqual(decision.outcome, PollingOutcome.SCHEDULED)
        self.assertEqual(decision.scheduled_for, event_starts_at - timedelta(hours=24))

    def test_set_enabled_updates_typed_payload_and_row_metadata_together(self) -> None:
        repository = PollingStrategyRepository()
        repository.save(market_strategy())

        disabled = repository.set_enabled(
            provider_id="provider-a",
            source_id="odds",
            target=PollingTarget.MARKET,
            engine=None,
            enabled=False,
        )

        row = PollingStrategyRow.objects.get()
        self.assertFalse(disabled.enabled)
        self.assertFalse(row.enabled)
        self.assertFalse(PollingStrategy.model_validate(row.payload).enabled)

    def test_malformed_persisted_payload_fails_closed_with_safe_error(self) -> None:
        payload = market_strategy().model_dump(mode="json")
        payload["api_key"] = "must-not-leak"
        PollingStrategyRow.objects.create(
            provider_id="provider-a",
            source_id="odds",
            target="market",
            engine="",
            enabled=True,
            payload=payload,
        )

        with self.assertRaisesRegex(
            PollingStrategyPersistenceError,
            "polling strategy configuration is unavailable",
        ) as error:
            PollingStrategyRepository().list()

        self.assertNotIn("must-not-leak", str(error.exception))


def _request(*, engine: str):
    from datetime import UTC, datetime
    from uuid import UUID

    from qbet.data.polling import PollingRequest

    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    return PollingRequest(
        source=SOURCE,
        target=PollingTarget.MARKET,
        match_id="match-1",
        correlation_id=UUID("12345678-1234-5678-1234-567812345678"),
        fetched_at=now - timedelta(minutes=10),
        event_starts_at=now + timedelta(hours=2),
        next_poll_at=now,
        attempt=0,
        mode="simulation",
        engine=engine,
    )
