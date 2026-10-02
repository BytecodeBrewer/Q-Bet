import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from qbet.data.models import DataSourceMetadata, FreshnessStatus
from qbet.data.polling import PollingTarget, SmartPollingPolicy
from qbet.data.polling import PollingOutcome, PollingStrategy, PollingStrategyResolver
from qbet.data.polling_runtime import PollingWork, SmartPollingRuntime
from qbet.data.the_odds_api import TheOddsApiAdapter


class Repository:
    def __init__(self):
        self.rows = {}

    def synchronize(self, active_work):
        for work in active_work:
            self.rows.setdefault(work.identity, work)
        return tuple(self.rows.values())

    def claim_due(self, *, now, limit, lease_for):
        return tuple(
            w
            for w in self.rows.values()
            if not w.disabled and not w.terminal and w.next_due_at <= now
        )[:limit]

    def save(self, work):
        self.rows[work.identity] = work
        return work


class Monitoring:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)
        return record


def test_discovery_handoff_survives_runtime_restart_and_existing_fetch_preserves_evidence():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    source = DataSourceMetadata(provider_id="the_odds_api", source_id="test", transport="api")
    parent = PollingWork(
        source=source,
        target=PollingTarget.MARKET,
        engine="sports_capital",
        mode="simulation",
        match_id="discovery:test",
        correlation_id=uuid4(),
        sport="tennis_atp",
        market="h2h",
        event_starts_at=now + timedelta(days=365),
        next_due_at=now,
        discovery=True,
        last_outcome="scheduled",
        discovery_limit=2,
    )
    urls = []

    def transport(url):
        urls.append(url)
        if "/events?" in url:
            return (
                200,
                {},
                json.dumps(
                    [
                        {
                            "id": event,
                            "sport_key": "tennis_atp",
                            "commence_time": (now + timedelta(hours=2)).isoformat(),
                        }
                        for event in ("one", "two", "one")
                    ]
                ).encode(),
            )
        event = "one" if "/events/one/" in url else "two"
        return (
            200,
            {},
            json.dumps(
                {
                    "id": event,
                    "sport_key": "tennis_atp",
                    "bookmakers": [
                        {
                            "key": "unknown",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "Home", "price": 2.1},
                                        {"name": "Away", "price": 2.1},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ).encode(),
        )

    repo, monitoring = Repository(), Monitoring()

    def runtime():
        return SmartPollingRuntime(
            repository=repo,
            monitoring_writer=monitoring,
            policy=SmartPollingPolicy(),
            collector=TheOddsApiAdapter(api_key="test", clock=lambda: now, http_get=transport),
        )

    result = runtime().tick(
        active_work=(parent,), now=now, limit=10, lease_for=timedelta(minutes=1)
    )
    assert result.provider_requests == 1
    restored = repo.rows[parent.identity]
    assert restored.last_reason == "discovery_candidates"
    assert {e.event_id for e in restored.discovered_events} == {"one", "two"}
    children = tuple(
        parent.model_copy(
            update={
                "discovery": False,
                "match_id": e.event_id,
                "event_starts_at": e.starts_at,
            }
        )
        for e in restored.discovered_events
    )
    runtime().tick(
        active_work=(restored, *children), now=now, limit=10, lease_for=timedelta(minutes=1)
    )
    runtime().tick(
        active_work=(restored, *children), now=now, limit=10, lease_for=timedelta(minutes=1)
    )
    assert len(repo.rows) == 3
    assert len(urls) == 3
    for child in children:
        stored = repo.rows[child.identity]
        assert stored.latest_snapshot.freshness is FreshnessStatus.UNKNOWN
        assert stored.last_reason == "polling_source_freshness_unknown"
        assert stored.latest_snapshot.offers[0].source_updated_at is None
    assert monitoring.records[-1].references["unmapped_providers"] == "1"


def test_discovery_failure_preserves_previous_targets_and_has_bounded_retry():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    parent = PollingWork(
        source=DataSourceMetadata(provider_id="the_odds_api", source_id="test", transport="api"),
        target=PollingTarget.MARKET,
        engine="sports_capital",
        mode="simulation",
        match_id="discovery:test",
        correlation_id=uuid4(),
        sport="tennis_atp",
        market="h2h",
        event_starts_at=now + timedelta(days=365),
        next_due_at=now,
        discovery=True,
        last_outcome="scheduled",
    )
    repo, monitoring, calls = Repository(), Monitoring(), []
    runtime = SmartPollingRuntime(
        repository=repo,
        monitoring_writer=monitoring,
        policy=SmartPollingPolicy(),
        collector=TheOddsApiAdapter(
            api_key="test",
            http_get=lambda url: (
                calls.append(url) or 429,
                {},
                b"",
            ),
        ),
    )
    for attempt in range(4):
        runtime.tick(
            active_work=(parent,),
            now=now + timedelta(minutes=5 * attempt),
            limit=1,
            lease_for=timedelta(minutes=1),
        )
    assert len(calls) == 3
    assert repo.rows[parent.identity].terminal
    assert repo.rows[parent.identity].last_reason == "retry_attempts_exhausted"


def test_discovery_policy_is_event_independent_and_preserves_gates():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    parent = PollingWork(
        source=DataSourceMetadata(provider_id="the_odds_api", source_id="test", transport="api"),
        target=PollingTarget.MARKET,
        engine="sports_capital",
        mode="simulation",
        match_id="discovery:test",
        correlation_id=uuid4(),
        sport="tennis_atp",
        market="h2h",
        event_starts_at=now - timedelta(days=1),
        next_due_at=now,
        discovery=True,
    )
    strategy = PollingStrategy(
        source=parent.source,
        target=parent.target,
        market_refresh_points=(timedelta(hours=24), timedelta(minutes=15)),
        freshness_window=timedelta(minutes=15),
    )

    def policy(**updates):
        return SmartPollingPolicy(
            resolver=PollingStrategyResolver((strategy.model_copy(update=updates),))
        )

    assert policy().decide(parent.request()).scheduled_for == now
    assert policy(enabled=False).decide(parent.request()).outcome is PollingOutcome.DISABLED
    assert (
        policy(capacity_units=0).decide(parent.request()).outcome
        is PollingOutcome.DEFERRED_CAPACITY
    )
    exhausted = parent.model_copy(update={"attempt": 3})
    assert policy().decide(exhausted.request()).outcome is PollingOutcome.RETRY_EXHAUSTED
    fresh = parent.model_copy(update={"last_success_at": now})
    assert policy().decide(fresh.request()).freshness_deadline == now + timedelta(minutes=15)
    due = fresh.request(evaluation_at=now + timedelta(minutes=15))
    assert policy().decide(due).outcome is PollingOutcome.SCHEDULED


def test_hosted_discovery_entry_repeats_with_all_actual_presets(monkeypatch):
    import django

    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "qbet.web.settings")
    monkeypatch.setenv("QBET_DATABASE_URL", "postgresql://qbet:qbet@127.0.0.1:5432/qbet_test")
    django.setup()
    from decimal import Decimal
    from unittest.mock import patch

    from django.test import RequestFactory, override_settings

    from qbet.data.models import DiscoveredEvent
    from qbet.web import polling_tick as hosted
    from qbet.web.polling_settings import POLLING_PRESETS
    from qbet.workflow.routing import (
        EngineModes,
        RoutingConfiguration,
        UserEngineModes,
        UserRoutingPreferences,
    )

    for preset in POLLING_PRESETS:
        now = datetime(2026, 10, 1, 15, 34, tzinfo=UTC)
        repo = Repository()
        repo.list = lambda: tuple(repo.rows.values())
        source = DataSourceMetadata(
            provider_id="the_odds_api", source_id="simulation-the-odds-api", transport="api"
        )
        strategy = PollingStrategy(
            source=source,
            target=PollingTarget.MARKET,
            freshness_window=timedelta(minutes=preset.freshness_minutes),
            market_refresh_points=tuple(
                timedelta(minutes=m) for m in preset.market_refresh_points_minutes
            ),
            max_attempts=preset.max_attempts,
        )
        calls = []

        def transport(url):
            calls.append(url)
            if "/events?" in url:
                events = [
                    DiscoveredEvent(event_id=e, sport="tennis_atp", starts_at=start)
                    for e in ("one", "two", "one")
                ]
                return (
                    200,
                    {},
                    json.dumps(
                        [
                            {
                                "id": e.event_id,
                                "sport_key": e.sport,
                                "commence_time": e.starts_at.isoformat(),
                            }
                            for e in events
                        ]
                    ).encode(),
                )
            event = "one" if "/events/one/" in url else "two"
            return (
                200,
                {},
                json.dumps(
                    {
                        "id": event,
                        "sport_key": "tennis_atp",
                        "bookmakers": [
                            {
                                "key": "unknown",
                                "markets": [
                                    {
                                        "key": "h2h",
                                        "last_update": now.isoformat(),
                                        "outcomes": [
                                            {"name": "Home", "price": 2.1},
                                            {"name": "Away", "price": 2.1},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ).encode(),
            )

        start = now + timedelta(hours=2)
        adapter = TheOddsApiAdapter(
            api_key="test", clock=lambda: now, available_stake=Decimal("100"), http_get=transport
        )
        with (
            override_settings(
                QBET_POLLING_TICK_TOKEN="test",
                QBET_POLLING_TICK_MAX_WORK=10,
                QBET_POLLING_CLAIM_SECONDS=120,
                QBET_POLLING_DEFER_SECONDS=300,
                QBET_SIMULATION_SPORTS_SOURCE="the_odds_api",
                QBET_POLLING_DISCOVERY_SPORTS=("tennis_atp",),
                QBET_POLLING_DISCOVERY_MAX_EVENTS=20,
                QBET_SIMULATION_ODDS_MARKET="h2h",
                QBET_SIMULATION_ASSUMED_LIQUIDITY="100",
            ),
            patch.object(hosted, "PostgresPollingWorkRepository", return_value=repo),
            patch.object(hosted, "PostgresMonitoringRepository", return_value=Monitoring()),
            patch.object(hosted, "PollingStrategyRepository") as strategies,
            patch.object(hosted, "RoutingConfigurationRepository") as routing,
            patch.object(hosted, "UserRoutingPreferenceRepository") as preferences,
            patch.object(hosted, "TheOddsApiAdapter", return_value=adapter),
            patch.object(hosted, "datetime") as clock,
        ):
            strategies.return_value.resolver.return_value = PollingStrategyResolver((strategy,))
            routing.return_value.load.return_value = RoutingConfiguration(
                sports_capital=EngineModes(simulation=True)
            )
            preferences.return_value.list.return_value = (
                ("owner", UserRoutingPreferences(sports_capital=UserEngineModes(simulation=True))),
            )

            def wake():
                clock.now.return_value = now
                response = hosted.polling_tick(
                    RequestFactory().post(
                        "/internal/polling/tick/", HTTP_AUTHORIZATION="Bearer test"
                    )
                )
                assert response.status_code == 200
                return json.loads(response.content)

            assert wake()["provider_requests"] == 1
            wake()
            assert wake()["provider_requests"] == 2
            assert wake()["provider_requests"] == 0
            assert len(repo.rows) == 3
            assert all(w.latest_snapshot is not None for w in repo.list() if not w.discovery)
            now += timedelta(minutes=preset.freshness_minutes)
            assert wake()["provider_requests"] == 1
            wake()
            assert len(repo.rows) == 3
            assert len(calls) == 4
