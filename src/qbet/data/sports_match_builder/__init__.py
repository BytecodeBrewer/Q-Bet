"""Deterministic conversion from normalized sports data to engine requests."""

from .builders import (
    build_dutching_match,
    build_legacy_exchange_hedged_free_bet_match,
    build_legacy_exchange_hedged_qualifying_bet_match,
    prepare_bonus_sportsbook_offers,
    prepare_german_bonus_sportsbook_offers,
    build_two_way_arbitrage_match,
)
from .models import (
    BuiltBonusMatch,
    BuiltSportsCapitalMatch,
    DutchingMatchMetadata,
    FreeBetMatchMetadata,
    PreparedBonusSportsbookOffers,
    QualifyingBetMatchMetadata,
    SportsMatchContext,
    TwoWayArbitrageMatchMetadata,
)

__all__ = [
    "BuiltBonusMatch",
    "BuiltSportsCapitalMatch",
    "DutchingMatchMetadata",
    "FreeBetMatchMetadata",
    "PreparedBonusSportsbookOffers",
    "QualifyingBetMatchMetadata",
    "SportsMatchContext",
    "TwoWayArbitrageMatchMetadata",
    "build_dutching_match",
    "build_legacy_exchange_hedged_free_bet_match",
    "build_legacy_exchange_hedged_qualifying_bet_match",
    "prepare_bonus_sportsbook_offers",
    "prepare_german_bonus_sportsbook_offers",
    "build_two_way_arbitrage_match",
]
