from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from qbet.data import (
    ApiAdapter,
    CompletenessStatus,
    DataCollectionRequest,
    DataCollector,
    DataSourceMetadata,
    DataTarget,
    DeterministicInMemoryDataSource,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    OfferAvailability,
    SourceTransport,
)


TIMESTAMP = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
SOURCE = DataSourceMetadata(
    provider_id="book-a",
    source_id="sports-feed",
    transport=SourceTransport.IN_MEMORY,
)


def offer(identifier: str, selection: str = "home") -> NormalizedOffer:
    return NormalizedOffer(
        id=identifier,
        market_id="market-1",
        selection=selection,
        odds=Decimal("2.15"),
        available_stake=Decimal("100"),
        currency="EUR",
        observed_at=TIMESTAMP,
    )


def snapshot(target: DataTarget = DataTarget.BONUS, **changes: object) -> NormalizedMarketSnapshot:
    values: dict[str, object] = {
        "id": f"{target.value}-snapshot",
        "correlation_id": UUID("12345678-1234-5678-1234-567812345678"),
        "target": target,
        "source": SOURCE,
        "sport": "football",
        "event_id": "event-1",
        "market_id": "market-1",
        "fetched_at": TIMESTAMP,
        "freshness": FreshnessStatus.FRESH,
        "completeness": CompletenessStatus.COMPLETE,
        "offers": (offer(f"{target.value}-offer"),),
    }
    values.update(changes)
    return NormalizedMarketSnapshot(**values)


def request(target: DataTarget, correlation_id: UUID) -> DataCollectionRequest:
    return DataCollectionRequest(correlation_id=correlation_id, target=target, source=SOURCE)


def test_valid_snapshot_is_ready_for_engine_specific_preparation() -> None:
    value = snapshot()

    assert value.require_ready_for_preparation() is value
    assert value.source.provider_id == "book-a"
    assert value.offers[0].currency == "EUR"


def test_invalid_or_incomplete_snapshot_fails_before_preparation() -> None:
    with pytest.raises(ValidationError, match="greater than 1"):
        NormalizedOffer.model_validate({**offer("invalid", selection="away").model_dump(), "odds": Decimal("1")})

    stale = snapshot(freshness=FreshnessStatus.STALE)
    with pytest.raises(ValueError, match="fresh"):
        stale.require_ready_for_preparation()

    partial = snapshot(completeness=CompletenessStatus.PARTIAL)
    with pytest.raises(ValueError, match="complete"):
        partial.require_ready_for_preparation()

    suspended = snapshot(
        offers=(
            offer("suspended").model_copy(
                update={"availability": OfferAvailability.SUSPENDED}
            ),
        )
    )
    with pytest.raises(ValueError, match="unavailable"):
        suspended.require_ready_for_preparation()


def test_snapshot_validates_metadata_timestamps_and_market_consistency() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        snapshot(fetched_at=datetime(2026, 8, 31, 9, 0))

    with pytest.raises(ValidationError, match="snapshot market_id"):
        snapshot(offers=(offer("other").model_copy(update={"market_id": "market-2"}),))


def test_deterministic_source_supplies_bonus_and_sports_capital_fixtures() -> None:
    source = DeterministicInMemoryDataSource(
        (snapshot(DataTarget.BONUS), snapshot(DataTarget.SPORTS_CAPITAL))
    )
    correlation_id = UUID("87654321-4321-8765-4321-876543218765")

    bonus = source.collect(request(DataTarget.BONUS, correlation_id))
    sports = source.fetch(request(DataTarget.SPORTS_CAPITAL, correlation_id))

    assert bonus.target is DataTarget.BONUS
    assert sports.target is DataTarget.SPORTS_CAPITAL
    assert bonus.correlation_id == correlation_id
    assert sports.correlation_id == correlation_id
    assert bonus.fetched_at == TIMESTAMP


def test_fake_source_implements_both_transport_protocols_without_network() -> None:
    source = DeterministicInMemoryDataSource((snapshot(),))

    assert isinstance(source, DataCollector)
    assert isinstance(source, ApiAdapter)
    with pytest.raises(KeyError, match="no deterministic snapshot"):
        source.collect(request(DataTarget.SPORTS_CAPITAL, UUID("11111111-1111-1111-1111-111111111111")))
