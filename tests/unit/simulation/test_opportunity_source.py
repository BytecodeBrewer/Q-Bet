from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from qbet.data import (
    CompletenessStatus,
    DataSourceMetadata,
    DataTarget,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    OfferAvailability,
    SourceTransport,
    THE_ODDS_API_PROVIDER_ID,
    TheOddsApiAuthenticationError,
    TheOddsApiConfigurationError,
    TheOddsApiPayloadError,
    TheOddsApiRateLimitError,
    TheOddsApiTransportError,
)
from qbet.simulation import SimulationEngine, SimulationRunConfig
from qbet.simulation.opportunity_source import (
    DeterministicSimulationOpportunitySource,
    SimulationOpportunitySourceError,
    TheOddsApiSportsSimulationConfig,
    TheOddsApiSportsSimulationOpportunitySource,
)

NOW = datetime(2026, 9, 19, 1, 0, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
SOURCE = DataSourceMetadata(
    provider_id=THE_ODDS_API_PROVIDER_ID,
    source_id="simulation-the-odds-api",
    transport=SourceTransport.API,
)


class RecordingCollector:
    def __init__(
        self,
        snapshot: NormalizedMarketSnapshot | None = None,
        error: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.error = error
        self.requests: list[object] = []

    def collect(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


def connected_config(**changes: object) -> TheOddsApiSportsSimulationConfig:
    values: dict[str, object] = {
        "sport": "tennis_atp",
        "event_id": "event-123",
        "market": "h2h",
        "assumed_liquidity": Decimal("100"),
        "requested_total_stake": Decimal("20"),
        "stake_precision": Decimal("0.01"),
    }
    values.update(changes)
    return TheOddsApiSportsSimulationConfig.model_validate(values)


def run_config(engine: SimulationEngine = SimulationEngine.SPORTS_CAPITAL) -> SimulationRunConfig:
    return SimulationRunConfig(engine=engine, starting_capital=Decimal("100"))


def market_snapshot(
    *,
    include_draw: bool = False,
    available_stake: Decimal = Decimal("100"),
) -> NormalizedMarketSnapshot:
    market_id = "event-123:h2h"
    offers = [
        NormalizedOffer(
            id="book-b:Home",
            market_id=market_id,
            selection="Home",
            provider="book-b",
            side="back",
            odds=Decimal("2.40"),
            available_stake=available_stake,
            currency="EUR",
            availability=OfferAvailability.AVAILABLE,
            observed_at=NOW,
        ),
        NormalizedOffer(
            id="book-a:Home",
            market_id=market_id,
            selection="Home",
            provider="book-a",
            side="back",
            odds=Decimal("2.40"),
            available_stake=available_stake,
            currency="EUR",
            availability=OfferAvailability.AVAILABLE,
            observed_at=NOW,
        ),
        NormalizedOffer(
            id="book-a:Away",
            market_id=market_id,
            selection="Away",
            provider="book-a",
            side="back",
            odds=Decimal("2.20"),
            available_stake=available_stake,
            currency="EUR",
            availability=OfferAvailability.AVAILABLE,
            observed_at=NOW,
        ),
        NormalizedOffer(
            id="book-c:Away",
            market_id=market_id,
            selection="Away",
            provider="book-c",
            side="back",
            odds=Decimal("2.50"),
            available_stake=available_stake,
            currency="EUR",
            availability=OfferAvailability.AVAILABLE,
            observed_at=NOW,
        ),
    ]
    if include_draw:
        offers.append(
            NormalizedOffer(
                id="book-d:Draw",
                market_id=market_id,
                selection="Draw",
                provider="book-d",
                side="back",
                odds=Decimal("3.10"),
                available_stake=available_stake,
                currency="EUR",
                availability=OfferAvailability.AVAILABLE,
                observed_at=NOW,
            )
        )
    return NormalizedMarketSnapshot(
        id=market_id,
        correlation_id=CORRELATION_ID,
        target=DataTarget.SPORTS_CAPITAL,
        source=SOURCE,
        sport="tennis_atp",
        event_id="event-123",
        market_id=market_id,
        fetched_at=NOW,
        freshness=FreshnessStatus.FRESH,
        completeness=CompletenessStatus.COMPLETE,
        offers=tuple(offers),
    )


def test_connected_source_selects_best_two_outcome_offers_deterministically() -> None:
    collector = RecordingCollector(market_snapshot())
    source = TheOddsApiSportsSimulationOpportunitySource(
        connected_config(),
        collector=collector,
    )

    bundle = source.build(run_config(), CORRELATION_ID)

    assert len(collector.requests) == 1
    request = bundle.opportunities[0]
    assert request.execution_offer_ids == ("book-c:Away", "book-a:Home")
    assert request.opportunity_id == "event-123:h2h"
    assert bundle.customer_report_input.transaction_id == str(CORRELATION_ID)
    assert {
        bundle.customer_report_input.provider,
        bundle.customer_report_input.counterparty_provider,
    } == {"book-a", "book-c"}


def test_connected_source_rejects_three_way_market_instead_of_reinterpreting_it() -> None:
    source = TheOddsApiSportsSimulationOpportunitySource(
        connected_config(),
        collector=RecordingCollector(market_snapshot(include_draw=True)),
    )

    with pytest.raises(SimulationOpportunitySourceError) as raised:
        source.build(run_config(), CORRELATION_ID)

    assert raised.value.reason_code == "simulation_market_requires_two_outcomes"


def test_connected_source_rejects_insufficient_configured_liquidity_before_provider_call() -> None:
    collector = RecordingCollector(market_snapshot())
    source = TheOddsApiSportsSimulationOpportunitySource(
        connected_config(
            assumed_liquidity=Decimal("10"),
            requested_total_stake=Decimal("20"),
        ),
        collector=collector,
    )

    with pytest.raises(SimulationOpportunitySourceError) as raised:
        source.build(run_config(), CORRELATION_ID)

    assert raised.value.reason_code == "simulation_liquidity_insufficient"
    assert collector.requests == []


@pytest.mark.parametrize(
    ("error", "reason_code"),
    [
        (
            TheOddsApiAuthenticationError("provider rejected credentials"),
            "simulation_odds_auth_failed",
        ),
        (
            TheOddsApiConfigurationError("configuration missing"),
            "simulation_odds_configuration_invalid",
        ),
        (
            TheOddsApiRateLimitError("provider quota exhausted"),
            "simulation_odds_rate_limited",
        ),
        (
            TheOddsApiTransportError("provider unavailable"),
            "simulation_odds_provider_unavailable",
        ),
        (
            TheOddsApiPayloadError("unsafe payload"),
            "simulation_odds_invalid_payload",
        ),
    ],
)
def test_connected_source_maps_provider_failures_to_safe_reason_codes(
    error: Exception,
    reason_code: str,
) -> None:
    source = TheOddsApiSportsSimulationOpportunitySource(
        connected_config(),
        collector=RecordingCollector(error=error),
    )

    with pytest.raises(SimulationOpportunitySourceError) as raised:
        source.build(run_config(), CORRELATION_ID)

    assert raised.value.reason_code == reason_code
    assert str(error) not in raised.value.user_message


def test_deterministic_bonus_source_remains_available_offline() -> None:
    bundle = DeterministicSimulationOpportunitySource(clock=lambda: NOW).build(
        run_config(SimulationEngine.BONUS),
        CORRELATION_ID,
    )

    assert len(bundle.opportunities) == 2
    assert bundle.customer_report_input.match == "Legacy exchange-hedged bonus fixture"
