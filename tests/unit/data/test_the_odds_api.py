from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError
from uuid import UUID

import pytest

from qbet.data import (
    ApiAdapter,
    DataCollectionRequest,
    DataCollector,
    DataSourceMetadata,
    DataTarget,
    SourceTransport,
    THE_ODDS_API_PROVIDER_ID,
    TheOddsApiAdapter,
    TheOddsApiAuthenticationError,
    TheOddsApiConfigurationError,
    TheOddsApiError,
    TheOddsApiPayloadError,
    TheOddsApiRateLimitError,
    TheOddsApiTransportError,
)
from qbet.data.sports_match_builder import (
    TwoWayArbitrageMatchMetadata,
    build_two_way_arbitrage_match,
)

NOW = datetime(2026, 9, 11, 10, 0, tzinfo=UTC)
SOURCE = DataSourceMetadata(
    provider_id=THE_ODDS_API_PROVIDER_ID,
    source_id="primary-odds-feed",
    transport=SourceTransport.API,
)


def request(**changes: object) -> DataCollectionRequest:
    values: dict[str, object] = {
        "correlation_id": UUID("12345678-1234-5678-1234-567812345678"),
        "target": DataTarget.SPORTS_CAPITAL,
        "source": SOURCE,
        "sport": "soccer_epl",
        "event_id": "event-123",
        "market": "h2h",
    }
    values.update(changes)
    return DataCollectionRequest.model_validate(values)


def payload(**changes: object) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": "event-123",
        "sport_key": "soccer_epl",
        "bookmakers": [
            {
                "key": "book-one",
                "markets": [
                    {
                        "key": "h2h",
                        "last_update": NOW.isoformat(),
                        "outcomes": [
                            {"name": "Home", "price": 2.25},
                            {"name": "Away", "price": 2.4},
                        ],
                    }
                ],
            }
        ],
    }
    value.update(changes)
    return value


def adapter(response: dict[str, Any], *, now: datetime = NOW) -> TheOddsApiAdapter:
    return TheOddsApiAdapter(
        api_key="configured-for-test",
        available_stake=Decimal("100"),
        clock=lambda: now,
        http_get=lambda _: (200, {}, json.dumps(response).encode()),
    )


def test_adapter_normalizes_one_provider_event_and_preserves_correlation() -> None:
    value = adapter(payload()).collect(request())

    assert isinstance(adapter(payload()), DataCollector)
    assert isinstance(adapter(payload()), ApiAdapter)
    assert value.correlation_id == request().correlation_id
    assert value.source == SOURCE
    assert value.id == "event-123:h2h"
    assert value.require_ready_for_preparation() is value
    assert value.offers[0].provider == "book-one"
    assert value.offers[0].odds == Decimal("2.25")


def test_normalized_snapshot_flows_through_existing_sports_match_builder() -> None:
    snapshot = adapter(payload()).fetch(request())

    match = build_two_way_arbitrage_match(
        snapshot,
        TwoWayArbitrageMatchMetadata(
            first_offer_id="book-one:Home",
            second_offer_id="book-one:Away",
            requested_total_stake=Decimal("10"),
            first_stake_precision=Decimal("0.01"),
            second_stake_precision=Decimal("0.01"),
            first_fee_rate=Decimal(0),
            second_fee_rate=Decimal(0),
        ),
    )

    assert match.context.correlation_id == request().correlation_id
    assert match.request.execution_offer_ids == ("book-one:Home", "book-one:Away")


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (payload(bookmakers=[]), "no bookmaker"),
        (payload(sport_key="other_sport"), "sport identity"),
        (
            payload(
                bookmakers=[
                    {
                        "key": "book-one",
                        "markets": [{"key": "h2h", "outcomes": [{"name": "Home", "price": 2.25}]}],
                    }
                ]
            ),
            "at least two outcomes",
        ),
    ],
)
def test_adapter_rejects_unsafe_provider_payloads(response: dict[str, Any], message: str) -> None:
    with pytest.raises(TheOddsApiPayloadError, match=message):
        adapter(response).fetch(request())


def test_adapter_uses_its_fetch_time_for_all_normalized_offers() -> None:
    response = payload()
    response["bookmakers"].append(
        {
            "key": "book-two",
            "markets": [
                {
                    "key": "h2h",
                    "last_update": NOW.isoformat(),
                    "outcomes": [
                        {"name": "Home", "price": 2.2},
                        {"name": "Away", "price": 2.45},
                    ],
                }
            ],
        }
    )

    snapshot = adapter(response).fetch(request())

    assert snapshot.fetched_at == NOW
    assert {offer.observed_at for offer in snapshot.offers} == {NOW}
    assert snapshot.require_ready_for_preparation() is snapshot


def test_adapter_requires_environment_or_application_bound_key() -> None:
    with pytest.raises(TheOddsApiConfigurationError, match="QBET_THE_ODDS_API_KEY"):
        TheOddsApiAdapter(
            http_get=lambda _: pytest.fail("network must not be called"),
            clock=lambda: NOW,
        ).fetch(request())


def test_adapter_requires_a_complete_provider_neutral_selection() -> None:
    with pytest.raises(TheOddsApiConfigurationError, match="sport, event_id, and market"):
        adapter(payload()).fetch(request(event_id=None))


def test_adapter_rejects_unsupported_lay_markets() -> None:
    value = TheOddsApiAdapter(
        api_key="configured-for-test",
        clock=lambda: NOW,
        http_get=lambda _: pytest.fail("network must not be called"),
    )

    with pytest.raises(TheOddsApiConfigurationError, match="lay markets"):
        value.fetch(request(market="h2h_lay"))


def test_adapter_encodes_provider_neutral_path_identifiers() -> None:
    captured_urls: list[str] = []
    provider_request = request(
        sport="soccer/europe?unexpected#fragment",
        event_id="event/123?unexpected#fragment%2F",
    )
    provider_payload = payload(
        id=provider_request.event_id,
        sport_key=provider_request.sport,
    )
    value = TheOddsApiAdapter(
        api_key="configured-for-test",
        clock=lambda: NOW,
        http_get=lambda url: (
            captured_urls.append(url) or 200,
            {},
            json.dumps(provider_payload).encode(),
        ),
    )

    value.fetch(provider_request)

    assert len(captured_urls) == 1
    assert "/sports/soccer%2Feurope%3Funexpected%23fragment/" in captured_urls[0]
    assert "/events/event%2F123%3Funexpected%23fragment%252F/odds?" in captured_urls[0]


def test_transport_failure_does_not_chain_or_expose_the_api_key() -> None:
    api_key = "test-key-that-must-not-leak"

    def rate_limited(url: str) -> tuple[int, dict[str, str], bytes]:
        raise HTTPError(url, 429, "Too Many Requests", {}, None)

    value = TheOddsApiAdapter(api_key=api_key, clock=lambda: NOW, http_get=rate_limited)

    with pytest.raises(TheOddsApiRateLimitError, match="request failed") as raised:
        value.fetch(request())

    assert isinstance(raised.value, TheOddsApiError)
    assert api_key not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, TheOddsApiAuthenticationError),
        (403, TheOddsApiAuthenticationError),
        (429, TheOddsApiRateLimitError),
        (503, TheOddsApiTransportError),
    ],
)
def test_adapter_classifies_non_success_responses(
    status: int, error_type: type[TheOddsApiError]
) -> None:
    value = TheOddsApiAdapter(
        api_key="configured-for-test",
        clock=lambda: NOW,
        http_get=lambda _: (status, {}, b""),
    )

    with pytest.raises(error_type, match=f"HTTP {status}"):
        value.fetch(request())


def test_adapter_rejects_invalid_json_and_mismatched_event_identity() -> None:
    invalid_json = TheOddsApiAdapter(
        api_key="configured-for-test",
        clock=lambda: NOW,
        http_get=lambda _: (200, {}, b"{"),
    )
    with pytest.raises(TheOddsApiPayloadError, match="invalid JSON"):
        invalid_json.fetch(request())

    with pytest.raises(TheOddsApiPayloadError, match="event identity"):
        adapter(payload(id="other-event")).fetch(request())


def test_adapter_rejects_invalid_odds() -> None:
    invalid_odds = payload()
    invalid_odds["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = "Infinity"
    with pytest.raises(TheOddsApiPayloadError, match="price must be finite"):
        adapter(invalid_odds).fetch(request())


def test_snapshot_validation_errors_are_translated_to_payload_error() -> None:
    invalid = payload(
        bookmakers=[
            {
                "key": "book-one",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Home", "price": 2.25},
                            {"name": "Home", "price": 2.4},
                        ],
                    }
                ],
            }
        ]
    )

    with pytest.raises(TheOddsApiPayloadError, match="payload validation failed"):
        adapter(invalid).fetch(request())


@pytest.mark.parametrize(
    "timestamp, expected",
    [
        (None, "unknown"),
        ((NOW - timedelta(minutes=10)).isoformat(), "stale"),
        ((NOW + timedelta(seconds=1)).isoformat(), "stale"),
        ((NOW - timedelta(seconds=10)).isoformat(), "fresh"),
    ],
)
def test_source_freshness_is_not_receipt_freshness(timestamp, expected) -> None:
    response = payload()
    response["bookmakers"][0]["markets"][0]["last_update"] = timestamp
    snapshot = adapter(response).fetch(request())
    assert snapshot.freshness.value == expected
    assert snapshot.fetched_at == NOW
    assert snapshot.offers[0].observed_at == NOW
    assert snapshot.offers[0].source_updated_at == (
        datetime.fromisoformat(timestamp) if timestamp else None
    )
    assert snapshot.offers[0].stake_capacity_basis == "simulation_assumed"
    assert not snapshot.offers[0].account_availability_verified
    assert not snapshot.offers[0].licensed_catalog_presence
    assert snapshot.offers[0].canonical_provider_id is None
    if expected != "fresh":
        with pytest.raises(ValueError, match="fresh"):
            snapshot.require_ready_for_preparation()


def test_malformed_source_timestamp_fails_closed() -> None:
    response = payload()
    response["bookmakers"][0]["markets"][0]["last_update"] = "not-a-time"
    with pytest.raises(TheOddsApiPayloadError, match="timestamp"):
        adapter(response).fetch(request())


def test_discovery_is_bounded_deduplicated_and_excludes_expired_events() -> None:
    events = [
        {"id": name, "sport_key": "soccer_epl", "commence_time": time.isoformat()}
        for name, time in [
            ("later", NOW + timedelta(hours=3)),
            ("first", NOW + timedelta(hours=1)),
            ("first", NOW + timedelta(hours=1)),
            ("expired", NOW - timedelta(minutes=1)),
        ]
    ]
    urls = []
    value = TheOddsApiAdapter(
        api_key="test",
        clock=lambda: NOW,
        http_get=lambda url: (urls.append(url) or 200, {}, json.dumps(events).encode()),
    )
    result = value.discover(request(event_id=None), limit=1)
    assert [e.event_id for e in result] == ["first"]
    assert len(urls) == 1
    assert "/sports/soccer_epl/events?" in urls[0]
    assert "/odds" not in urls[0]


@pytest.mark.parametrize(
    "status, error",
    [
        (429, TheOddsApiRateLimitError),
        (503, TheOddsApiTransportError),
    ],
)
def test_discovery_classifies_provider_errors(status, error) -> None:
    value = TheOddsApiAdapter(api_key="test", http_get=lambda _: (status, {}, b""))
    with pytest.raises(error):
        value.discover(request(event_id=None), limit=2)


def test_reported_exhausted_quota_stops_further_requests_in_the_same_wake() -> None:
    calls = []
    value = TheOddsApiAdapter(
        api_key="test",
        http_get=lambda url: (
            calls.append(url) or 200,
            {"x-requests-remaining": "0"},
            b"[]",
        ),
    )
    assert value.discover(request(event_id=None), limit=2) == ()
    with pytest.raises(TheOddsApiRateLimitError, match="exhausted"):
        value.fetch(request())
    assert len(calls) == 1


def test_expired_discovery_event_is_retained_as_terminal_evidence() -> None:
    value = TheOddsApiAdapter(
        api_key="test",
        clock=lambda: NOW,
        http_get=lambda _: (
            200,
            {},
            json.dumps(
                [
                    {
                        "id": "expired",
                        "sport_key": "soccer_epl",
                        "commence_time": (NOW - timedelta(minutes=1)).isoformat(),
                    }
                ]
            ).encode(),
        ),
    )
    result = value.discover(request(event_id=None), limit=2)
    assert result[0].starts_at < NOW
