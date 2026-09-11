from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
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


def test_adapter_requires_environment_or_application_bound_key() -> None:
    with pytest.raises(TheOddsApiConfigurationError, match="QBET_THE_ODDS_API_KEY"):
        TheOddsApiAdapter(
            http_get=lambda _: pytest.fail("network must not be called"),
            clock=lambda: NOW,
        ).fetch(request())


def test_adapter_requires_a_complete_provider_neutral_selection() -> None:
    with pytest.raises(TheOddsApiConfigurationError, match="sport, event_id, and market"):
        adapter(payload()).fetch(request(event_id=None))
