import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from qbet.data.models import DataSourceMetadata, FreshnessStatus
from qbet.data.polling import PollingTarget, SmartPollingPolicy
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
