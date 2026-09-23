"""Pure sports match builders from normalized market data to engine requests."""

from __future__ import annotations

from decimal import Decimal

from qbet.calculations import (
    ArbitrageOffer,
    DutchingInput,
    DutchingOffer,
    FreeBetInput,
    QualifyingBetInput,
    TwoWayArbitrageInput,
)
from qbet.data.models import DataTarget, NormalizedMarketSnapshot, NormalizedOffer
from qbet.domain import OfferSide
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest

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


def build_legacy_exchange_hedged_qualifying_bet_match(
    snapshot: NormalizedMarketSnapshot,
    metadata: QualifyingBetMatchMetadata,
) -> BuiltBonusMatch:
    """Retained bookmaker-BACK/exchange-LAY qualifying strategy for regression only."""
    _require_target(snapshot, DataTarget.BONUS)
    back_offer, lay_offer = _selected_back_lay_pair(
        snapshot, metadata.back_offer_id, metadata.lay_offer_id
    )
    _require_available_stake(back_offer, metadata.back_stake)
    _require_available_stake(lay_offer, metadata.minimum_lay_available_stake)
    request = BonusEngineRequest(
        opportunity_id=snapshot.id,
        inputs=QualifyingBetInput(
            back_odds=back_offer.odds,
            lay_odds=lay_offer.odds,
            back_stake=metadata.back_stake,
            exchange_commission=metadata.exchange_commission,
            stake_precision=metadata.stake_precision,
            max_lay_liability=metadata.max_lay_liability,
        ),
        currency=back_offer.currency,
        execution_offer_ids=(back_offer.id, lay_offer.id),
    )
    return BuiltBonusMatch(request=request, context=_context(snapshot))


def build_legacy_exchange_hedged_free_bet_match(
    snapshot: NormalizedMarketSnapshot,
    metadata: FreeBetMatchMetadata,
) -> BuiltBonusMatch:
    """Retained bookmaker-BACK/exchange-LAY free-bet strategy for regression only."""
    _require_target(snapshot, DataTarget.BONUS)
    back_offer, lay_offer = _selected_back_lay_pair(
        snapshot, metadata.back_offer_id, metadata.lay_offer_id
    )
    _require_available_stake(back_offer, metadata.free_bet_amount)
    _require_available_stake(lay_offer, metadata.minimum_lay_available_stake)
    request = BonusEngineRequest(
        opportunity_id=snapshot.id,
        inputs=FreeBetInput(
            free_bet_amount=metadata.free_bet_amount,
            back_odds=back_offer.odds,
            lay_odds=lay_offer.odds,
            exchange_commission=metadata.exchange_commission,
            stake_precision=metadata.stake_precision,
            stake_return_rule=metadata.stake_return_rule,
        ),
        currency=back_offer.currency,
        execution_offer_ids=(back_offer.id, lay_offer.id),
    )
    return BuiltBonusMatch(request=request, context=_context(snapshot))


def prepare_bonus_sportsbook_offers(
    snapshot: NormalizedMarketSnapshot,
    offer_ids: tuple[str, ...],
) -> PreparedBonusSportsbookOffers:
    """Validate canonical BonusEngine fixed-odds sportsbook preparation."""

    _require_target(snapshot, DataTarget.BONUS)
    if len(offer_ids) < 2 or len(set(offer_ids)) != len(offer_ids):
        raise ValueError("bonus sportsbook preparation requires distinct offer ids")
    offers = tuple(_offer_by_id(snapshot, identifier) for identifier in offer_ids)
    _require_distinct_outcomes(offers)
    _require_fixed_odds_sportsbook_offers(offers)
    if len({offer.provider for offer in offers}) < 2:
        raise ValueError("bonus sportsbook preparation requires multiple sportsbooks")
    _require_consistent_currency(offers)
    return PreparedBonusSportsbookOffers(offers=offers, context=_context(snapshot))


def build_two_way_arbitrage_match(
    snapshot: NormalizedMarketSnapshot,
    metadata: TwoWayArbitrageMatchMetadata,
) -> BuiltSportsCapitalMatch:
    _require_target(snapshot, DataTarget.SPORTS_CAPITAL)
    first_offer, second_offer = _selected_pair(
        snapshot, metadata.first_offer_id, metadata.second_offer_id
    )
    _require_fixed_odds_sportsbook_offers((first_offer, second_offer))
    _require_available_stake(first_offer, metadata.requested_total_stake)
    _require_available_stake(second_offer, metadata.requested_total_stake)
    request = SportsCapitalEngineRequest(
        opportunity_id=snapshot.id,
        inputs=TwoWayArbitrageInput(
            first_offer=_arbitrage_offer(
                first_offer, metadata.first_stake_precision, metadata.first_fee_rate
            ),
            second_offer=_arbitrage_offer(
                second_offer, metadata.second_stake_precision, metadata.second_fee_rate
            ),
            requested_total_stake=metadata.requested_total_stake,
        ),
        currency=first_offer.currency,
        execution_offer_ids=(first_offer.id, second_offer.id),
    )
    return BuiltSportsCapitalMatch(request=request, context=_context(snapshot))


def build_dutching_match(
    snapshot: NormalizedMarketSnapshot,
    metadata: DutchingMatchMetadata,
) -> BuiltSportsCapitalMatch:
    _require_target(snapshot, DataTarget.SPORTS_CAPITAL)
    offers = tuple(_offer_by_id(snapshot, identifier) for identifier in metadata.offer_ids)
    _require_distinct_outcomes(offers)
    _require_fixed_odds_sportsbook_offers(offers)
    _require_selected_offers_cover_snapshot(snapshot, offers)
    _require_consistent_currency(offers)
    for offer in offers:
        _require_available_stake(offer, metadata.minimum_available_stake)
    dutching_offers = tuple(
        DutchingOffer(
            outcome=offer.selection,
            odds=offer.odds,
            available_liquidity=offer.available_stake,
            stake_precision=precision,
            fee_rate=fee_rate,
            currency=offer.currency,
        )
        for offer, precision, fee_rate in zip(
            offers, metadata.stake_precisions, metadata.fee_rates, strict=True
        )
    )
    request = SportsCapitalEngineRequest(
        opportunity_id=snapshot.id,
        inputs=DutchingInput(
            offers=dutching_offers,
            target_mode=metadata.target_mode,
            total_stake=metadata.total_stake,
            target_return=metadata.target_return,
            outcomes_are_exhaustive=True,
        ),
        currency=offers[0].currency,
        execution_offer_ids=metadata.offer_ids,
    )
    return BuiltSportsCapitalMatch(request=request, context=_context(snapshot))


def _context(snapshot: NormalizedMarketSnapshot) -> SportsMatchContext:
    return SportsMatchContext(
        correlation_id=snapshot.correlation_id,
        snapshot_id=snapshot.id,
        provider_id=snapshot.source.provider_id,
        source_id=snapshot.source.source_id,
        event_id=snapshot.event_id,
        market_id=snapshot.market_id,
    )


def _require_target(snapshot: NormalizedMarketSnapshot, target: DataTarget) -> None:
    snapshot.require_ready_for_preparation()
    if snapshot.target is not target:
        raise ValueError(f"market snapshot target must be {target.value}")


def _selected_pair(
    snapshot: NormalizedMarketSnapshot,
    first_id: str,
    second_id: str,
) -> tuple[NormalizedOffer, NormalizedOffer]:
    first_offer = _offer_by_id(snapshot, first_id)
    second_offer = _offer_by_id(snapshot, second_id)
    if first_offer.selection == second_offer.selection:
        raise ValueError("selected offers must represent distinct outcomes")
    _require_consistent_currency((first_offer, second_offer))
    return first_offer, second_offer


def _selected_back_lay_pair(
    snapshot: NormalizedMarketSnapshot,
    back_offer_id: str,
    lay_offer_id: str,
) -> tuple[NormalizedOffer, NormalizedOffer]:
    back_offer = _offer_by_id(snapshot, back_offer_id)
    lay_offer = _offer_by_id(snapshot, lay_offer_id)
    if back_offer.id == lay_offer.id:
        raise ValueError("selected offers must use distinct offer records")
    if back_offer.selection != lay_offer.selection:
        raise ValueError("selected offers must represent the same outcome")
    if back_offer.side is not OfferSide.BACK or lay_offer.side is not OfferSide.LAY:
        raise ValueError(
            "legacy exchange-hedged preparation requires a bookmaker back and exchange lay pair"
        )
    if back_offer.provider == lay_offer.provider:
        raise ValueError("back and lay offers must use distinct providers")
    _require_consistent_currency((back_offer, lay_offer))
    return back_offer, lay_offer


def _offer_by_id(snapshot: NormalizedMarketSnapshot, identifier: str) -> NormalizedOffer:
    try:
        return next(offer for offer in snapshot.offers if offer.id == identifier)
    except StopIteration as error:
        raise ValueError(f"snapshot does not contain offer {identifier}") from error


def _require_distinct_outcomes(offers: tuple[NormalizedOffer, ...]) -> None:
    if len({offer.selection for offer in offers}) != len(offers):
        raise ValueError("selected offers must represent distinct outcomes")


def _require_selected_offers_cover_snapshot(
    snapshot: NormalizedMarketSnapshot,
    offers: tuple[NormalizedOffer, ...],
) -> None:
    selected_ids = {offer.id for offer in offers}
    snapshot_ids = {offer.id for offer in snapshot.offers}
    if selected_ids != snapshot_ids:
        raise ValueError("dutching selection must cover every snapshot outcome")


def _require_fixed_odds_sportsbook_offers(
    offers: tuple[NormalizedOffer, ...],
) -> None:
    if any(offer.side is not OfferSide.BACK for offer in offers):
        raise ValueError(
            "sportsbook preparation requires fixed-odds sportsbook back offers"
        )


def _require_consistent_currency(offers: tuple[NormalizedOffer, ...]) -> None:
    if len({offer.currency for offer in offers}) != 1:
        raise ValueError("selected offers must use the same currency")


def _require_available_stake(offer: NormalizedOffer, required_stake: Decimal) -> None:
    if offer.available_stake < required_stake:
        raise ValueError(f"offer {offer.id} has insufficient available stake")


def _arbitrage_offer(
    offer: NormalizedOffer, stake_precision: Decimal, fee_rate: Decimal
) -> ArbitrageOffer:
    return ArbitrageOffer(
        outcome=offer.selection,
        odds=offer.odds,
        available_liquidity=offer.available_stake,
        stake_precision=stake_precision,
        fee_rate=fee_rate,
        currency=offer.currency,
    )
