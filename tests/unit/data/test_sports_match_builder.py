from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from qbet.calculations import (
    DutchingInput,
    DutchingTargetMode,
    FreeBetInput,
    FreeBetStakeReturn,
    QualifyingBetInput,
    TwoWayArbitrageInput,
)
from qbet.data import (
    CompletenessStatus,
    DataSourceMetadata,
    DataTarget,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    OfferAvailability,
    SourceTransport,
)
from qbet.data.sports_match_builder import (
    DutchingMatchMetadata,
    FreeBetMatchMetadata,
    QualifyingBetMatchMetadata,
    TwoWayArbitrageMatchMetadata,
    build_dutching_match,
    build_free_bet_match,
    build_qualifying_bet_match,
    build_two_way_arbitrage_match,
)
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest

TIMESTAMP = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
SOURCE = DataSourceMetadata(
    provider_id="book-a", source_id="sports-feed", transport=SourceTransport.IN_MEMORY
)


def offer(
    identifier: str, selection: str, odds: str, available_stake: str = "100"
) -> NormalizedOffer:
    return NormalizedOffer(
        id=identifier,
        market_id="market-1",
        selection=selection,
        odds=Decimal(odds),
        available_stake=Decimal(available_stake),
        currency="EUR",
        observed_at=TIMESTAMP,
    )


def snapshot(
    target: DataTarget, offers: tuple[NormalizedOffer, ...], **changes: object
) -> NormalizedMarketSnapshot:
    values: dict[str, object] = {
        "id": "snapshot-1",
        "correlation_id": UUID("12345678-1234-5678-1234-567812345678"),
        "target": target,
        "source": SOURCE,
        "sport": "football",
        "event_id": "event-1",
        "market_id": "market-1",
        "fetched_at": TIMESTAMP,
        "freshness": FreshnessStatus.FRESH,
        "completeness": CompletenessStatus.COMPLETE,
        "offers": offers,
    }
    values.update(changes)
    return NormalizedMarketSnapshot.model_validate(values)


def test_build_qualifying_bet_match_preserves_context_and_is_deterministic() -> None:
    value = snapshot(
        DataTarget.BONUS, (offer("back", "home", "2.4"), offer("lay", "away", "2.5"))
    )
    metadata = QualifyingBetMatchMetadata(
        back_offer_id="back",
        lay_offer_id="lay",
        back_stake=Decimal(10),
        exchange_commission=Decimal("0.02"),
        stake_precision=Decimal("0.01"),
        max_lay_liability=Decimal(100),
        minimum_lay_available_stake=Decimal(10),
    )

    prepared = build_qualifying_bet_match(value, metadata)

    assert isinstance(prepared.request, BonusEngineRequest)
    assert isinstance(prepared.request.inputs, QualifyingBetInput)
    assert prepared.request.execution_offer_ids == ("back", "lay")
    assert prepared.context.correlation_id == value.correlation_id
    assert prepared.context.provider_id == "book-a"
    assert (
        build_qualifying_bet_match(value, metadata).request.inputs
        == prepared.request.inputs
    )


def test_build_free_bet_match_requires_explicit_promotion_rule() -> None:
    value = snapshot(
        DataTarget.BONUS, (offer("back", "home", "3.0"), offer("lay", "away", "3.2"))
    )
    metadata = FreeBetMatchMetadata(
        back_offer_id="back",
        lay_offer_id="lay",
        free_bet_amount=Decimal(15),
        exchange_commission=Decimal("0.02"),
        stake_precision=Decimal("0.01"),
        minimum_lay_available_stake=Decimal(10),
        stake_return_rule=FreeBetStakeReturn.STAKE_NOT_RETURNED,
    )

    prepared = build_free_bet_match(value, metadata)

    assert isinstance(prepared.request.inputs, FreeBetInput)
    assert (
        prepared.request.inputs.stake_return_rule
        is FreeBetStakeReturn.STAKE_NOT_RETURNED
    )


def test_build_two_way_arbitrage_match_creates_existing_engine_request() -> None:
    value = snapshot(
        DataTarget.SPORTS_CAPITAL,
        (offer("home", "home", "2.2"), offer("away", "away", "2.3")),
    )
    metadata = TwoWayArbitrageMatchMetadata(
        first_offer_id="home",
        second_offer_id="away",
        requested_total_stake=Decimal(50),
        first_stake_precision=Decimal("0.01"),
        second_stake_precision=Decimal("0.01"),
    )

    prepared = build_two_way_arbitrage_match(value, metadata)

    assert isinstance(prepared.request, SportsCapitalEngineRequest)
    assert isinstance(prepared.request.inputs, TwoWayArbitrageInput)
    assert prepared.request.inputs.first_offer.outcome == "home"
    assert prepared.context.event_id == "event-1"


def test_build_dutching_match_supports_exhaustive_multi_outcome_contract() -> None:
    value = snapshot(
        DataTarget.SPORTS_CAPITAL,
        (
            offer("home", "home", "3.2"),
            offer("draw", "draw", "3.4"),
            offer("away", "away", "3.3"),
        ),
    )
    metadata = DutchingMatchMetadata(
        offer_ids=("home", "draw", "away"),
        target_mode=DutchingTargetMode.TOTAL_STAKE,
        total_stake=Decimal(30),
        minimum_available_stake=Decimal(30),
        stake_precisions=(Decimal("0.01"),) * 3,
        fee_rates=(Decimal(0),) * 3,
    )

    prepared = build_dutching_match(value, metadata)

    assert isinstance(prepared.request.inputs, DutchingInput)
    assert prepared.request.inputs.outcomes_are_exhaustive is True
    assert len(prepared.request.execution_offer_ids) == 3


@pytest.mark.parametrize(
    ("changes", "metadata", "message"),
    [
        (
            {"freshness": FreshnessStatus.STALE},
            QualifyingBetMatchMetadata(
                back_offer_id="back",
                lay_offer_id="lay",
                back_stake=Decimal(10),
                exchange_commission=Decimal("0.02"),
                stake_precision=Decimal("0.01"),
                max_lay_liability=Decimal(100),
                minimum_lay_available_stake=Decimal(10),
            ),
            "fresh",
        ),
        (
            {"completeness": CompletenessStatus.PARTIAL},
            QualifyingBetMatchMetadata(
                back_offer_id="back",
                lay_offer_id="lay",
                back_stake=Decimal(10),
                exchange_commission=Decimal("0.02"),
                stake_precision=Decimal("0.01"),
                max_lay_liability=Decimal(100),
                minimum_lay_available_stake=Decimal(10),
            ),
            "complete",
        ),
        (
            {
                "offers": (
                    offer("back", "home", "2.4"),
                    offer("lay", "away", "2.5").model_copy(
                        update={"availability": OfferAvailability.SUSPENDED}
                    ),
                )
            },
            QualifyingBetMatchMetadata(
                back_offer_id="back",
                lay_offer_id="lay",
                back_stake=Decimal(10),
                exchange_commission=Decimal("0.02"),
                stake_precision=Decimal("0.01"),
                max_lay_liability=Decimal(100),
                minimum_lay_available_stake=Decimal(10),
            ),
            "unavailable",
        ),
    ],
)
def test_sports_match_builder_rejects_unready_snapshots(
    changes: dict[str, object], metadata: QualifyingBetMatchMetadata, message: str
) -> None:
    values = {
        "offers": (offer("back", "home", "2.4"), offer("lay", "away", "2.5")),
        **changes,
    }
    value = snapshot(DataTarget.BONUS, **values)

    with pytest.raises(ValueError, match=message):
        build_qualifying_bet_match(value, metadata)


def test_sports_match_builder_rejects_incompatible_selection_currency_and_liquidity() -> (
    None
):
    bonus = snapshot(
        DataTarget.BONUS,
        (offer("back", "home", "2.4", "5"), offer("same", "home", "2.5")),
    )
    metadata = QualifyingBetMatchMetadata(
        back_offer_id="back",
        lay_offer_id="same",
        back_stake=Decimal(10),
        exchange_commission=Decimal("0.02"),
        stake_precision=Decimal("0.01"),
        max_lay_liability=Decimal(100),
        minimum_lay_available_stake=Decimal(10),
    )
    with pytest.raises(ValueError, match="distinct outcomes"):
        build_qualifying_bet_match(bonus, metadata)

    sports = snapshot(
        DataTarget.SPORTS_CAPITAL,
        (
            offer("home", "home", "2.2"),
            offer("away", "away", "2.3").model_copy(update={"currency": "GBP"}),
        ),
    )
    arbitrage = TwoWayArbitrageMatchMetadata(
        first_offer_id="home",
        second_offer_id="away",
        requested_total_stake=Decimal(50),
        first_stake_precision=Decimal("0.01"),
        second_stake_precision=Decimal("0.01"),
    )
    with pytest.raises(ValueError, match="same currency"):
        build_two_way_arbitrage_match(sports, arbitrage)

    insufficient = snapshot(
        DataTarget.SPORTS_CAPITAL,
        (offer("home", "home", "2.2", "20"), offer("away", "away", "2.3", "100")),
    )
    with pytest.raises(ValueError, match="insufficient"):
        build_two_way_arbitrage_match(insufficient, arbitrage)


def test_sports_match_builder_rejects_wrong_engine_target() -> None:
    value = snapshot(
        DataTarget.BONUS, (offer("home", "home", "2.2"), offer("away", "away", "2.3"))
    )
    metadata = TwoWayArbitrageMatchMetadata(
        first_offer_id="home",
        second_offer_id="away",
        requested_total_stake=Decimal(50),
        first_stake_precision=Decimal("0.01"),
        second_stake_precision=Decimal("0.01"),
    )

    with pytest.raises(ValueError, match="sports_capital"):
        build_two_way_arbitrage_match(value, metadata)
