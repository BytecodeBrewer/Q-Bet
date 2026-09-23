from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from qbet.data.models import (
    CompletenessStatus,
    DataSourceMetadata,
    DataTarget,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    OfferAvailability,
    SourceTransport,
)
from qbet.domain.models import OfferSide
from qbet.providers import load_german_sportsbook_catalog
from qbet.providers.bonus import BonusProviderEligibilityError, canonicalize_german_bonus_snapshot


def snapshot(provider_key: str) -> NormalizedMarketSnapshot:
    now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    return NormalizedMarketSnapshot(
        id="event:h2h",
        correlation_id=UUID("12345678-1234-5678-1234-567812345678"),
        target=DataTarget.BONUS,
        source=DataSourceMetadata(
            provider_id="the_odds_api",
            source_id="primary-odds-feed",
            transport=SourceTransport.API,
        ),
        sport="soccer_germany_bundesliga",
        event_id="event",
        market_id="event:h2h",
        fetched_at=now,
        freshness=FreshnessStatus.FRESH,
        completeness=CompletenessStatus.COMPLETE,
        offers=(
            NormalizedOffer(
                id=f"{provider_key}:home",
                market_id="event:h2h",
                selection="home",
                provider=provider_key,
                side=OfferSide.BACK,
                odds=Decimal("2.1"),
                available_stake=Decimal("100"),
                currency="EUR",
                availability=OfferAvailability.AVAILABLE,
                observed_at=now,
            ),
            NormalizedOffer(
                id=f"{provider_key}:away",
                market_id="event:h2h",
                selection="away",
                provider=provider_key,
                side=OfferSide.BACK,
                odds=Decimal("2.2"),
                available_stake=Decimal("100"),
                currency="EUR",
                availability=OfferAvailability.AVAILABLE,
                observed_at=now,
            ),
        ),
    )


def test_bonus_snapshot_replaces_verified_external_key_with_canonical_provider() -> None:
    value = canonicalize_german_bonus_snapshot(
        snapshot("tipico_de"),
        load_german_sportsbook_catalog(),
    )

    assert {offer.provider for offer in value.offers} == {"tipico"}


def test_bonus_snapshot_fails_closed_for_unmapped_or_exchange_identity() -> None:
    catalog = load_german_sportsbook_catalog()

    for external_key in ("book-a", "betfair_ex_eu"):
        with pytest.raises(BonusProviderEligibilityError) as raised:
            canonicalize_german_bonus_snapshot(snapshot(external_key), catalog)
        assert raised.value.reason.value == "unmapped_external_identity"
