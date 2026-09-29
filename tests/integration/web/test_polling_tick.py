from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from qbet.data.models import (
    CompletenessStatus,
    DataSourceMetadata,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    OfferAvailability,
    SourceTransport,
)
from qbet.data.polling import PollingStrategy, PollingTarget
from qbet.data.the_odds_api import TheOddsApiTransportError
from qbet.monitoring import MonitoringQuery
from qbet.storage.ledger import RoutingConfigurationRepository, UserRoutingPreferenceRepository
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.storage.polling import PollingStrategyRepository
from qbet.storage.polling_work import PostgresPollingWorkRepository
from qbet.web.polling_tick import configured_polling_work
from qbet.workflow.routing import (
    EngineModes,
    RoutingConfiguration,
    UserEngineModes,
    UserRoutingPreferences,
)


class RecordingCollector:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.requests = []
        self.error = error

    def collect(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        fetched_at = datetime.now(UTC)
        market_id = f"{request.event_id}:{request.market}"
        offers = tuple(
            NormalizedOffer(
                id=f"offer-{index}",
                market_id=market_id,
                selection=selection,
                provider=f"provider-{index}",
                side="back",
                odds=Decimal("2.10"),
                available_stake=Decimal("100"),
                currency="EUR",
                availability=OfferAvailability.AVAILABLE,
                observed_at=fetched_at,
            )
            for index, selection in enumerate(("home", "away"), start=1)
        )
        return NormalizedMarketSnapshot(
            id=f"snapshot-{request.event_id}",
            correlation_id=request.correlation_id,
            target=request.target,
            source=request.source,
            sport=request.sport,
            event_id=request.event_id,
            market_id=market_id,
            fetched_at=fetched_at,
            freshness=FreshnessStatus.FRESH,
            completeness=CompletenessStatus.COMPLETE,
            offers=offers,
        )


class PollingTickEndpointTests(TestCase):
    def setUp(self) -> None:
        self.owner = "polling-owner"
        self.source_settings = {
            "QBET_POLLING_TICK_TOKEN": "test-polling-token",
            "QBET_POLLING_TICK_MAX_WORK": 10,
            "QBET_POLLING_CLAIM_SECONDS": 120,
            "QBET_POLLING_DEFER_SECONDS": 300,
            "QBET_SIMULATION_SPORTS_SOURCE": "the_odds_api",
            "QBET_SIMULATION_ODDS_SPORT": "soccer_germany_bundesliga",
            "QBET_SIMULATION_ODDS_EVENT_ID": "event-123",
            "QBET_SIMULATION_ODDS_MARKET": "h2h",
            "QBET_SIMULATION_ODDS_EVENT_STARTS_AT": (
                datetime.now(UTC) + timedelta(hours=2)
            ).isoformat(),
            "QBET_SIMULATION_ASSUMED_LIQUIDITY": "100",
        }
        RoutingConfigurationRepository().save(
            RoutingConfiguration(
                sports_capital=EngineModes(simulation=True),
            )
        )
        UserRoutingPreferenceRepository().save(
            self.owner,
            UserRoutingPreferences(
                sports_capital=UserEngineModes(simulation=True),
            ),
        )
        source = DataSourceMetadata(
            provider_id="the_odds_api",
            source_id="simulation-the-odds-api",
            transport=SourceTransport.API,
        )
        PollingStrategyRepository().save(
            PollingStrategy(
                source=source,
                target=PollingTarget.MARKET,
                engine="sports_capital",
                enabled=True,
                freshness_window=timedelta(minutes=5),
                market_interval=timedelta(minutes=1),
                latest_market_poll_before_event=timedelta(minutes=1),
                result_retry_interval=timedelta(minutes=10),
                max_attempts=3,
            )
        )

    @override_settings(QBET_POLLING_TICK_TOKEN="test-polling-token")
    def test_tick_rejects_missing_or_wrong_bearer_token(self) -> None:
        missing = self.client.post("/internal/polling/tick/")
        wrong = self.client.post(
            "/internal/polling/tick/",
            HTTP_AUTHORIZATION="Bearer wrong-token",
        )

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(PostgresPollingWorkRepository().list(), ())

    def test_first_tick_schedules_without_calling_provider(self) -> None:
        collector = RecordingCollector()
        with self.settings(**self.source_settings):
            with patch("qbet.web.polling_tick.TheOddsApiAdapter", return_value=collector):
                response = self.client.post(
                    "/internal/polling/tick/",
                    HTTP_AUTHORIZATION="Bearer test-polling-token",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider_requests"], 0)
        self.assertEqual(response.json()["outcomes"], ["scheduled"])
        self.assertEqual(collector.requests, [])
        stored = PostgresPollingWorkRepository().list()[0]
        self.assertEqual(stored.last_outcome, "scheduled")
        self.assertGreater(stored.next_due_at, datetime.now(UTC) - timedelta(seconds=5))

    def test_due_tick_calls_provider_once_and_persists_snapshot_and_activity(self) -> None:
        collector = RecordingCollector()
        now = datetime.now(UTC)
        with self.settings(**self.source_settings):
            candidate = configured_polling_work(now=now)[0].model_copy(
                update={
                    "next_due_at": now - timedelta(minutes=2),
                    "last_outcome": "scheduled",
                    "last_reason": "market_refresh_due",
                }
            )
            PostgresPollingWorkRepository().synchronize((candidate,))
            with patch("qbet.web.polling_tick.TheOddsApiAdapter", return_value=collector):
                response = self.client.post(
                    "/internal/polling/tick/",
                    HTTP_AUTHORIZATION="Bearer test-polling-token",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider_requests"], 1)
        self.assertEqual(len(collector.requests), 1)
        stored = PostgresPollingWorkRepository().list()[0]
        self.assertEqual(stored.last_outcome, "success")
        self.assertIsNotNone(stored.latest_snapshot)
        self.assertIsNotNone(stored.last_success_at)

        records = PostgresMonitoringRepository().list_records(
            MonitoringQuery(
                start=now - timedelta(minutes=1),
                end=datetime.now(UTC) + timedelta(minutes=1),
                correlation_id=stored.correlation_id,
            )
        )
        self.assertEqual([record.status for record in records[-2:]], ["fetching", "success"])

    def test_provider_unavailable_is_persisted_and_never_reported_as_success(self) -> None:
        collector = RecordingCollector(error=TheOddsApiTransportError("provider detail"))
        now = datetime.now(UTC)
        with self.settings(**self.source_settings):
            candidate = configured_polling_work(now=now)[0].model_copy(
                update={
                    "next_due_at": now - timedelta(minutes=2),
                    "last_outcome": "scheduled",
                    "last_reason": "market_refresh_due",
                }
            )
            PostgresPollingWorkRepository().synchronize((candidate,))
            with patch("qbet.web.polling_tick.TheOddsApiAdapter", return_value=collector):
                response = self.client.post(
                    "/internal/polling/tick/",
                    HTTP_AUTHORIZATION="Bearer test-polling-token",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider_requests"], 1)
        stored = PostgresPollingWorkRepository().list()[0]
        self.assertEqual(stored.last_outcome, "unavailable")
        self.assertEqual(stored.last_reason, "polling_provider_unavailable")
        self.assertEqual(stored.attempt, 1)
        self.assertIsNone(stored.last_success_at)
        self.assertNotIn("provider detail", response.content.decode())

    def test_fresh_due_work_is_skipped_without_provider_request(self) -> None:
        collector = RecordingCollector()
        now = datetime.now(UTC)
        with self.settings(**self.source_settings):
            candidate = configured_polling_work(now=now)[0].model_copy(
                update={
                    "next_due_at": now - timedelta(minutes=1),
                    "last_success_at": now,
                }
            )
            PostgresPollingWorkRepository().synchronize((candidate,))
            with patch("qbet.web.polling_tick.TheOddsApiAdapter", return_value=collector):
                response = self.client.post(
                    "/internal/polling/tick/",
                    HTTP_AUTHORIZATION="Bearer test-polling-token",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider_requests"], 0)
        self.assertEqual(collector.requests, [])
        stored = PostgresPollingWorkRepository().list()[0]
        self.assertEqual(stored.last_outcome, "skipped_fresh")
        self.assertGreater(stored.next_due_at, now)

    def test_disabling_user_route_preserves_work_but_prevents_new_provider_call(self) -> None:
        collector = RecordingCollector()
        now = datetime.now(UTC)
        with self.settings(**self.source_settings):
            candidate = configured_polling_work(now=now)[0].model_copy(
                update={
                    "next_due_at": now - timedelta(minutes=2),
                    "last_outcome": "scheduled",
                    "last_reason": "market_refresh_due",
                }
            )
            PostgresPollingWorkRepository().synchronize((candidate,))
            UserRoutingPreferenceRepository().save(
                self.owner,
                UserRoutingPreferences(
                    sports_capital=UserEngineModes(simulation=False),
                ),
            )
            with patch("qbet.web.polling_tick.TheOddsApiAdapter", return_value=collector):
                response = self.client.post(
                    "/internal/polling/tick/",
                    HTTP_AUTHORIZATION="Bearer test-polling-token",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processed"], 0)
        self.assertEqual(collector.requests, [])
        stored = PostgresPollingWorkRepository().list()[0]
        self.assertTrue(stored.disabled)
        self.assertFalse(stored.terminal)

    def test_tick_work_count_is_bounded(self) -> None:
        collector = RecordingCollector()
        UserRoutingPreferenceRepository().save(
            "second-owner",
            UserRoutingPreferences(
                sports_capital=UserEngineModes(simulation=True),
            ),
        )
        now = datetime.now(UTC)
        settings_values = dict(self.source_settings)
        settings_values["QBET_POLLING_TICK_MAX_WORK"] = 1
        with self.settings(**settings_values):
            candidates = tuple(
                candidate.model_copy(
                    update={
                        "next_due_at": now - timedelta(minutes=2),
                        "last_outcome": "scheduled",
                        "last_reason": "market_refresh_due",
                    }
                )
                for candidate in configured_polling_work(now=now)
            )
            PostgresPollingWorkRepository().synchronize(candidates)
            with patch("qbet.web.polling_tick.TheOddsApiAdapter", return_value=collector):
                response = self.client.post(
                    "/internal/polling/tick/",
                    HTTP_AUTHORIZATION="Bearer test-polling-token",
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["processed"], 1)
        self.assertEqual(response.json()["provider_requests"], 1)
        self.assertEqual(len(collector.requests), 1)
