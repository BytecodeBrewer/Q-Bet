from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.test import TestCase

from qbet.data.models import (
    CompletenessStatus,
    DataCollectionRequest,
    DataSourceMetadata,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    SourceTransport,
)
from qbet.data.polling import (
    PollingCapacityClass,
    PollingStrategy,
    PollingTarget,
)
from qbet.data.polling_runtime import SmartPollingRuntime
from qbet.data.polling_work import PollingMarketSelection, PollingWorkItem, PollingWorkState
from qbet.data.the_odds_api import THE_ODDS_API_PROVIDER_ID
from qbet.domain.models import OfferSide
from qbet.storage.ledger import (
    RoutingConfigurationRepository,
    UserRoutingPreferenceRepository,
)
from qbet.storage.models import MonitoringRecordRow, PollingWorkRow
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.storage.polling import PollingStrategyRepository, PollingWorkRepository
from qbet.workflow.routing import (
    EngineModes,
    RoutingConfiguration,
    UserEngineModes,
    UserRoutingPreferences,
)

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
FETCHED = NOW + timedelta(minutes=5)
SOURCE = DataSourceMetadata(
    provider_id=THE_ODDS_API_PROVIDER_ID,
    source_id="hosted-polling-test",
    transport=SourceTransport.API,
)
SELECTION = PollingMarketSelection(
    sport="soccer_epl",
    event_id="event-207",
    market="h2h",
    event_starts_at=NOW + timedelta(hours=2),
)


class _Collector:
    def __init__(self, *, fetched_at: datetime = FETCHED) -> None:
        self.fetched_at = fetched_at
        self.calls: list[DataCollectionRequest] = []

    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot:
        self.calls.append(request)
        market_id = f"{request.event_id}:{request.market}"
        return NormalizedMarketSnapshot(
            id=market_id,
            correlation_id=request.correlation_id,
            target=request.target,
            source=request.source,
            sport=request.sport,
            event_id=request.event_id,
            market_id=market_id,
            fetched_at=self.fetched_at,
            freshness=FreshnessStatus.FRESH,
            completeness=CompletenessStatus.COMPLETE,
            offers=(
                NormalizedOffer(
                    id="book-a:home",
                    market_id=market_id,
                    selection="Home",
                    provider="book-a",
                    side=OfferSide.BACK,
                    odds=Decimal("2.10"),
                    available_stake=Decimal("0"),
                    currency="EUR",
                    observed_at=self.fetched_at,
                ),
                NormalizedOffer(
                    id="book-b:away",
                    market_id=market_id,
                    selection="Away",
                    provider="book-b",
                    side=OfferSide.BACK,
                    odds=Decimal("2.10"),
                    available_stake=Decimal("0"),
                    currency="EUR",
                    observed_at=self.fetched_at,
                ),
            ),
        )


def _strategy() -> PollingStrategy:
    return PollingStrategy(
        source=SOURCE,
        target=PollingTarget.MARKET,
        engine="sports_capital",
        enabled=True,
        freshness_window=timedelta(minutes=5),
        market_refresh_points=(),
        market_interval=timedelta(minutes=5),
        latest_market_poll_before_event=timedelta(minutes=1),
        result_retry_interval=timedelta(minutes=10),
        max_attempts=3,
        capacity_class=PollingCapacityClass.FREE,
        capacity_units=100,
        request_cost_units=1,
    )


def _routing(enabled: bool = True) -> RoutingConfiguration:
    return RoutingConfiguration(
        sports_capital=EngineModes(simulation=enabled),
    )


def _preferences(enabled: bool = True) -> UserRoutingPreferences:
    return UserRoutingPreferences(
        sports_capital=UserEngineModes(simulation=enabled),
    )


class HostedPollingRuntimeTests(TestCase):
    def setUp(self) -> None:
        PollingStrategyRepository().save(_strategy())
        RoutingConfigurationRepository().save(_routing())
        UserRoutingPreferenceRepository().save("alice", _preferences())

    def _runtime(self, collector: _Collector) -> SmartPollingRuntime:
        return SmartPollingRuntime(
            work_store=PollingWorkRepository(),
            strategy_store=PollingStrategyRepository(),
            collectors={THE_ODDS_API_PROVIDER_ID: collector},
            monitoring_writer=PostgresMonitoringRepository(),
        )

    def _tick(self, runtime: SmartPollingRuntime, *, now: datetime, max_work: int = 10):
        routing = RoutingConfigurationRepository().load()
        assert routing is not None
        return runtime.tick(
            selection=SELECTION,
            routing=routing,
            preferences=UserRoutingPreferenceRepository().list(),
            now=now,
            max_work=max_work,
        )

    def test_tick_schedules_then_fetches_once_and_persists_normalized_state(self) -> None:
        collector = _Collector()
        runtime = self._runtime(collector)

        first = self._tick(runtime, now=NOW)

        self.assertEqual(first.eligible_routes, 1)
        self.assertEqual(first.claimed, 1)
        self.assertEqual(first.provider_calls, 0)
        self.assertEqual(PollingWorkRow.objects.count(), 1)
        first_row = PollingWorkRow.objects.get()
        self.assertEqual(first_row.state, PollingWorkState.PENDING.value)
        self.assertEqual(first_row.next_due_at, FETCHED)

        second = self._tick(runtime, now=FETCHED)

        self.assertEqual(second.provider_calls, 1)
        self.assertEqual(second.successes, 1)
        self.assertEqual(len(collector.calls), 1)
        persisted = PollingWorkRepository().load(first_row.work_id)
        assert persisted is not None
        self.assertEqual(persisted.last_outcome, "success")
        self.assertEqual(persisted.last_successful_fetch_at, FETCHED)
        self.assertEqual(persisted.snapshot.event_id, SELECTION.event_id)
        self.assertEqual(persisted.next_due_at, FETCHED + timedelta(minutes=5))

        repeat = self._tick(runtime, now=FETCHED)

        self.assertEqual(repeat.claimed, 0)
        self.assertEqual(len(collector.calls), 1)
        self.assertEqual(PollingWorkRow.objects.count(), 1)
        statuses = tuple(
            payload["status"]
            for payload in MonitoringRecordRow.objects.order_by("occurred_at", "id").values_list(
                "payload", flat=True
            )
        )
        self.assertIn("scheduled", statuses)
        self.assertIn("fetching", statuses)
        self.assertIn("success", statuses)

    def test_fresh_due_work_is_skipped_without_provider_request(self) -> None:
        collector = _Collector(fetched_at=NOW)
        work = PollingWorkItem.create_market(
            owner="alice",
            source=SOURCE,
            engine="sports_capital",
            mode="simulation",
            selection=SELECTION,
            next_due_at=NOW,
        )
        fresh_request = work.request.model_copy(
            update={"fetched_at": NOW, "next_poll_at": NOW}
        )
        PollingWorkRepository().ensure(
            work.model_copy(
                update={
                    "request": fresh_request,
                    "last_successful_fetch_at": NOW,
                }
            )
        )

        summary = self._tick(self._runtime(collector), now=NOW)

        self.assertEqual(summary.provider_calls, 0)
        self.assertEqual(summary.deferred, 1)
        self.assertEqual(collector.calls, [])
        persisted = PollingWorkRepository().load(work.id)
        assert persisted is not None
        self.assertEqual(persisted.last_outcome, "skipped_fresh")
        self.assertEqual(persisted.next_due_at, NOW + timedelta(minutes=5))

    def test_global_disable_preserves_history_and_blocks_due_provider_work(self) -> None:
        collector = _Collector()
        runtime = self._runtime(collector)
        self._tick(runtime, now=NOW)
        self._tick(runtime, now=FETCHED)
        self.assertEqual(len(collector.calls), 1)

        RoutingConfigurationRepository().save(_routing(enabled=False))
        summary = self._tick(runtime, now=FETCHED + timedelta(minutes=5))

        self.assertEqual(summary.provider_calls, 0)
        self.assertEqual(len(collector.calls), 1)
        row = PollingWorkRow.objects.get()
        persisted = PollingWorkRepository().load(row.work_id)
        assert persisted is not None
        self.assertEqual(persisted.state, PollingWorkState.DISABLED)
        self.assertEqual(persisted.last_reason, "polling_route_disabled")
        self.assertIsNotNone(persisted.snapshot)

    def test_tick_claims_only_the_configured_bounded_work_count(self) -> None:
        UserRoutingPreferenceRepository().save("bob", _preferences())
        collector = _Collector()

        summary = self._tick(self._runtime(collector), now=NOW, max_work=1)

        self.assertEqual(summary.eligible_routes, 2)
        self.assertEqual(summary.claimed, 1)
        self.assertEqual(PollingWorkRow.objects.count(), 2)
        self.assertEqual(
            PollingWorkRow.objects.filter(next_due_at__lte=NOW).count(),
            1,
        )
