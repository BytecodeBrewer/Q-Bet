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
    TheOddsApiConfigurationError,
    TheOddsApiError,
    TheOddsApiPayloadError,
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
                        "last_update": "2026-09-11T09:58:00Z",
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
            "last_update",
        ),
    ],
)
def test_adapter_rejects_unsafe_provider_payloads(response: dict[str, Any], message: str) -> None:
    with pytest.raises(TheOddsApiPayloadError, match=message):
        adapter(response).fetch(request())


def test_adapter_returns_stale_snapshot_that_builder_rejects() -> None:
    snapshot = adapter(payload(), now=NOW + timedelta(minutes=6)).fetch(request())

    with pytest.raises(ValueError, match="fresh"):
        snapshot.require_ready_for_preparation()


def test_adapter_uses_oldest_offer_timestamp_for_snapshot_freshness() -> None:
    response = payload()
    response["bookmakers"].append(
        {
            "key": "book-two",
            "markets": [
                {
                    "key": "h2h",
                    "last_update": "2026-09-11T09:50:00Z",
                    "outcomes": [
                        {"name": "Home", "price": 2.2},
                        {"name": "Away", "price": 2.45},
                    ],
                }
            ],
        }
    )

    snapshot = adapter(response).fetch(request())

    with pytest.raises(ValueError, match="fresh"):
        snapshot.require_ready_for_preparation()


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

    with pytest.raises(TheOddsApiError, match="request failed") as raised:
        value.fetch(request())

    assert api_key not in str(raised.value)
    assert raised.value.__cause__ is None


def test_non_success_response_is_a_stable_provider_error() -> None:
    value = TheOddsApiAdapter(
        api_key="configured-for-test",
        clock=lambda: NOW,
        http_get=lambda _: (429, {}, b""),
    )

    with pytest.raises(TheOddsApiError, match="HTTP 429"):
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


def test_adapter_rejects_invalid_odds_and_future_provider_timestamps() -> None:
    invalid_odds = payload()
    invalid_odds["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = "Infinity"
    with pytest.raises(TheOddsApiPayloadError, match="price must be finite"):
        adapter(invalid_odds).fetch(request())

    future_timestamp = payload()
    future_timestamp["bookmakers"][0]["markets"][0]["last_update"] = "2026-09-11T10:01:00Z"
    with pytest.raises(TheOddsApiPayloadError, match="must not be in the future"):
        adapter(future_timestamp).fetch(request())
