"""Compose user-owned Bonus Offers with fresh fixed-odds sportsbook market data."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol, cast
from uuid import UUID

from django.utils import timezone
from qbet.calculations import (
    ArbitrageOffer,
    FreeBetStakeReturn,
    SportsbookFreeBetInput,
    SportsbookQualifyingBetInput,
)
from qbet.data import (
    DataCollectionRequest,
    DataSourceMetadata,
    DataTarget,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    SourceTransport,
    THE_ODDS_API_PROVIDER_ID,
    TheOddsApiAdapter,
    TheOddsApiError,
)
from qbet.data.sports_match_builder import prepare_german_bonus_sportsbook_offers
from qbet.domain.models import Currency, DomainModel, Identifier, PositiveDecimal
from qbet.domain.verification import ProviderState
from qbet.engines import BonusEngineRequest
from qbet.reporting import CustomerReportAmount, CustomerReportInput
from qbet.simulation.models import SimulationEngine, SimulationRunConfig
from qbet.simulation.opportunity_source import (
    SimulationOpportunityBundle,
    SimulationOpportunitySourceError,
)
from qbet.storage.providers import PostgresSportsbookCatalogRepository
from qbet.web.models import BonusOffer


class BonusMarketCollector(Protocol):
    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot: ...


class BonusOfferSimulationConfig(DomainModel):
    sport: Identifier
    event_id: Identifier
    market: Identifier
    assumed_liquidity: PositiveDecimal
    stake_precision: PositiveDecimal


class BonusOfferSimulationOpportunitySource:
    """Build one promotion-aware BonusEngine opportunity for one authenticated owner."""

    def __init__(
        self,
        *,
        user_id: int,
        config: BonusOfferSimulationConfig,
        collector: BonusMarketCollector | None = None,
        catalog_repository: PostgresSportsbookCatalogRepository | None = None,
    ) -> None:
        self._user_id = user_id
        self._config = config
        self._collector = collector or TheOddsApiAdapter(
            available_stake=config.assumed_liquidity
        )
        self._catalog_repository = (
            catalog_repository or PostgresSportsbookCatalogRepository()
        )

    def build(
        self,
        config: SimulationRunConfig,
        correlation_id: UUID,
    ) -> SimulationOpportunityBundle:
        if config.engine is not SimulationEngine.BONUS:
            raise _source_error(
                "bonus_source_engine_mismatch",
                "Bonus Offer market data can only feed BonusEngine Simulation.",
            )

        offer = self._active_offer()
        try:
            catalog = self._catalog_repository.load()
        except OSError:
            raise _source_error(
                "bonus_provider_catalog_unavailable",
                "Sportsbook eligibility is temporarily unavailable.",
            ) from None

        request = DataCollectionRequest(
            correlation_id=correlation_id,
            target=DataTarget.BONUS,
            source=DataSourceMetadata(
                provider_id=THE_ODDS_API_PROVIDER_ID,
                source_id="bonus-simulation-the-odds-api",
                transport=SourceTransport.API,
            ),
            sport=self._config.sport,
            event_id=self._config.event_id,
            market=self._config.market,
        )
        try:
            snapshot = self._collector.collect(request)
        except TheOddsApiError:
            raise _source_error(
                "bonus_market_provider_unavailable",
                "Fresh BonusEngine market data is temporarily unavailable.",
            ) from None

        try:
            snapshot.require_ready_for_preparation()
        except ValueError:
            raise _source_error(
                "bonus_market_not_ready",
                "BonusEngine requires a fresh and complete market snapshot.",
            ) from None

        if len({item.selection for item in snapshot.offers}) != 2:
            raise _source_error(
                "bonus_market_requires_two_outcomes",
                "Current fixed-odds BonusEngine hedging supports two-outcome markets only.",
            )
        if offer.wagering_requirement not in (None, Decimal(0)):
            raise _source_error(
                "bonus_offer_conditions_unsupported",
                "This offer contains a turnover condition that is not yet part of BonusEngine math.",
            )

        raw_promotion, raw_hedge = self._select_pair(snapshot, offer, catalog)
        try:
            prepared = prepare_german_bonus_sportsbook_offers(
                snapshot,
                (raw_promotion.id, raw_hedge.id),
                catalog,
            )
        except ValueError:
            raise _source_error(
                "bonus_market_preparation_failed",
                "Compatible sportsbook offers could not be prepared safely.",
            ) from None

        by_id = {item.id: item for item in prepared.offers}
        promotion = by_id[raw_promotion.id]
        hedge = by_id[raw_hedge.id]
        promotion_offer = _calculation_offer(promotion, self._config.stake_precision)
        hedge_offer = _calculation_offer(hedge, self._config.stake_precision)

        if offer.promotion_type == BonusOffer.PromotionType.QUALIFYING_BET:
            if offer.required_stake is None:
                raise _source_error(
                    "bonus_offer_invalid",
                    "The qualifying Bonus Offer is missing its required stake.",
                )
            inputs: SportsbookQualifyingBetInput | SportsbookFreeBetInput
            inputs = SportsbookQualifyingBetInput(
                promotion_offer=promotion_offer,
                hedge_offer=hedge_offer,
                qualifying_stake=offer.required_stake,
            )
            recorded_amount = offer.required_stake
            recorded_label = "Qualifying stake"
            strategy = "Qualifying bet"
        elif offer.promotion_type == BonusOffer.PromotionType.FREE_BET:
            if offer.promotion_value is None or not offer.stake_return_rule:
                raise _source_error(
                    "bonus_offer_invalid",
                    "The free-bet Bonus Offer is missing required promotion terms.",
                )
            inputs = SportsbookFreeBetInput(
                promotion_offer=promotion_offer,
                hedge_offer=hedge_offer,
                free_bet_amount=offer.promotion_value,
                stake_return_rule=FreeBetStakeReturn(offer.stake_return_rule),
            )
            recorded_amount = offer.promotion_value
            recorded_label = "Promotion amount"
            strategy = "Free bet"
        else:
            raise _source_error(
                "bonus_offer_type_unsupported",
                "This Bonus Offer type is not supported by BonusEngine.",
            )

        engine_request = BonusEngineRequest(
            opportunity_id=f"{snapshot.id}:bonus-offer-{offer.pk}",
            inputs=inputs,
            currency=cast(Currency, offer.currency),
            execution_offer_ids=(promotion.id, hedge.id),
        )
        return SimulationOpportunityBundle(
            opportunities=(engine_request,),
            provider_state=ProviderState(
                provider_id=offer.provider.provider_id,
                active_bets_count=0,
            ),
            customer_report_input=CustomerReportInput(
                match=f"{self._config.sport} / {self._config.event_id}",
                provider=offer.provider.display_name,
                counterparty_provider=hedge.provider,
                strategy=strategy,
                assigned_amounts=(
                    CustomerReportAmount(label=recorded_label, amount=recorded_amount),
                ),
                invested_capital=recorded_amount,
                currency=offer.currency,
                transaction_id=str(correlation_id),
            ),
        )

    def _active_offer(self) -> BonusOffer:
        active = tuple(
            BonusOffer.objects.filter(
                user_id=self._user_id,
                valid_until__gt=timezone.now(),
            )
            .select_related("provider")
            .prefetch_related("provider__external_identities")
            .order_by("valid_until", "id")
        )
        if not active:
            raise _source_error(
                "bonus_offer_missing",
                "Create an active Bonus Offer before starting BonusEngine Simulation.",
            )
        for offer in active:
            if offer.is_preparation_ready:
                return offer
        raise _source_error(
            "bonus_offer_unavailable",
            "No active Bonus Offer is currently eligible for API-backed preparation.",
        )

    def _select_pair(self, snapshot, offer: BonusOffer, catalog):
        canonical: list[tuple[NormalizedOffer, NormalizedOffer]] = []
        for raw in snapshot.offers:
            resolution = catalog.resolve(
                source_id=snapshot.source.provider_id,
                external_key=raw.provider,
            )
            if resolution.eligible and resolution.provider is not None:
                canonical.append(
                    (raw, raw.model_copy(update={"provider": resolution.provider.provider_id}))
                )

        minimum_odds = offer.minimum_odds
        promotion_candidates = [
            pair
            for pair in canonical
            if pair[1].provider == offer.provider.provider_id
            and pair[1].currency == offer.currency
            and (minimum_odds is None or pair[1].odds >= minimum_odds)
        ]
        candidate_pairs: list[
            tuple[NormalizedOffer, NormalizedOffer, NormalizedOffer, NormalizedOffer]
        ] = []
        for raw_promotion, promotion in promotion_candidates:
            for raw_hedge, hedge in canonical:
                if (
                    hedge.selection != promotion.selection
                    and hedge.provider != promotion.provider
                    and hedge.currency == promotion.currency
                ):
                    candidate_pairs.append(
                        (raw_promotion, raw_hedge, promotion, hedge)
                    )
        if not candidate_pairs:
            raise _source_error(
                "bonus_market_no_compatible_offer",
                "No fresh opposing sportsbook offer is compatible with this Bonus Offer.",
            )
        selected = max(
            candidate_pairs,
            key=lambda pair: (
                pair[2].odds * pair[3].odds,
                pair[2].odds,
                pair[3].odds,
                pair[0].id,
                pair[1].id,
            ),
        )
        return selected[0], selected[1]


def _calculation_offer(
    offer: NormalizedOffer,
    stake_precision: Decimal,
) -> ArbitrageOffer:
    return ArbitrageOffer(
        outcome=offer.selection,
        odds=offer.odds,
        available_liquidity=offer.available_stake,
        stake_precision=stake_precision,
        currency=offer.currency,
    )


def _source_error(reason_code: Identifier, message: str) -> SimulationOpportunitySourceError:
    return SimulationOpportunitySourceError(reason_code, message)
