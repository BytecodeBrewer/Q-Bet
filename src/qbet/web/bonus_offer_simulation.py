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
    SportsbookFreeBetResult,
    SportsbookQualifyingBetInput,
    SportsbookQualifyingBetResult,
    SportsbookTaxTreatment,
    calculate_sportsbook_free_bet,
    calculate_sportsbook_qualifying_bet,
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
from qbet.providers import SportsbookCatalog
from qbet.engines import BonusEngineRequest, BonusOfferDependency
from qbet.reporting import (
    CustomerReportAmount,
    CustomerReportFinancialTerm,
    CustomerReportInput,
)
from qbet.simulation.models import SimulationEngine, SimulationRunConfig
from qbet.simulation.opportunity_source import (
    SimulationOpportunityBundle,
    SimulationOpportunitySourceError,
)
from qbet.storage.postgres import PostgresProviderStateRepository
from qbet.storage.protocol import ProviderStateRepository
from qbet.storage.providers import PostgresSportsbookCatalogRepository
from qbet.web.bonus_financial_terms import (
    SettingsSportsbookFinancialProfileRepository,
    SportsbookFinancialProfile,
    SportsbookFinancialProfileRepository,
)
from qbet.web.models import BonusOffer


class BonusMarketCollector(Protocol):
    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot: ...


class BonusOfferSimulationConfig(DomainModel):
    sport: Identifier
    event_id: Identifier
    market: Identifier
    assumed_liquidity: PositiveDecimal
    stake_precision: PositiveDecimal


BonusPreviewResult = SportsbookQualifyingBetResult | SportsbookFreeBetResult


class BonusOfferSimulationOpportunitySource:
    """Build one promotion-aware BonusEngine opportunity for one authenticated owner."""

    def __init__(
        self,
        *,
        user_id: int,
        config: BonusOfferSimulationConfig,
        collector: BonusMarketCollector | None = None,
        catalog_repository: PostgresSportsbookCatalogRepository | None = None,
        provider_state_repository: ProviderStateRepository | None = None,
        financial_profile_repository: SportsbookFinancialProfileRepository | None = None,
    ) -> None:
        self._user_id = user_id
        self._config = config
        self._collector = collector or TheOddsApiAdapter(
            available_stake=config.assumed_liquidity
        )
        self._catalog_repository = (
            catalog_repository or PostgresSportsbookCatalogRepository()
        )
        self._provider_state_repository = (
            provider_state_repository or PostgresProviderStateRepository()
        )
        self._financial_profile_repository = (
            financial_profile_repository
            or SettingsSportsbookFinancialProfileRepository()
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

        offers = self._active_offers()
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

        last_market_error: SimulationOpportunitySourceError | None = None
        for offer in offers:
            try:
                return self._build_offer_bundle(
                    snapshot=snapshot,
                    offer=offer,
                    catalog=catalog,
                    correlation_id=correlation_id,
                )
            except SimulationOpportunitySourceError as error:
                if error.reason_code != "bonus_market_no_compatible_offer":
                    raise
                last_market_error = error

        if last_market_error is not None:
            raise last_market_error
        raise _source_error(
            "bonus_market_no_compatible_offer",
            "No active Bonus Offer is compatible with the fresh sportsbook market.",
        )

    def _build_offer_bundle(
        self,
        *,
        snapshot: NormalizedMarketSnapshot,
        offer: BonusOffer,
        catalog: SportsbookCatalog,
        correlation_id: UUID,
    ) -> SimulationOpportunityBundle:
        provider_state = self._provider_state(offer.provider.provider_id)
        promotion_profile = self._required_financial_profile(
            offer.provider.provider_id
        )
        if offer.wagering_requirement not in (None, Decimal(0)):
            raise _source_error(
                "bonus_offer_conditions_unsupported",
                "This offer contains a turnover condition that is not yet part of BonusEngine math.",
            )

        raw_promotion, raw_hedge, hedge_profile = self._select_pair(
            snapshot,
            offer,
            catalog,
            promotion_profile,
        )
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
        promotion_offer = _calculation_offer(
            promotion,
            self._config.stake_precision,
            promotion_profile.fee_rate,
        )
        hedge_offer = _calculation_offer(
            hedge,
            self._config.stake_precision,
            hedge_profile.fee_rate,
        )

        inputs, preview, strategy = _build_bonus_inputs(
            offer,
            promotion_offer,
            hedge_offer,
            promotion_profile,
            hedge_profile,
        )
        engine_request = BonusEngineRequest(
            opportunity_id=(
                f"{snapshot.id}:bonus-offer-{offer.pk}:v{offer.version}"
            ),
            inputs=inputs,
            currency=cast(Currency, offer.currency),
            execution_offer_ids=(promotion.id, hedge.id),
            bonus_offer_dependency=BonusOfferDependency(
                offer_id=cast(int, offer.pk),
                offer_version=offer.version,
                owner_id=self._user_id,
                sport=self._config.sport,
                event_id=self._config.event_id,
                market=self._config.market,
            ),
        )

        assigned_amounts = _report_amounts(preview)
        return SimulationOpportunityBundle(
            opportunities=(engine_request,),
            provider_state=provider_state,
            customer_report_input=CustomerReportInput(
                match=f"{self._config.sport} / {self._config.event_id}",
                provider=offer.provider.display_name,
                counterparty_provider=_provider_display_name(catalog, hedge.provider),
                strategy=strategy,
                assigned_amounts=assigned_amounts,
                financial_terms=(
                    _report_financial_term(
                        offer.provider.display_name,
                        promotion_profile,
                    ),
                    _report_financial_term(
                        _provider_display_name(catalog, hedge.provider),
                        hedge_profile,
                    ),
                ),
                invested_capital=preview.capital_required,
                currency=offer.currency,
                transaction_id=str(correlation_id),
            ),
        )

    def _active_offers(self) -> tuple[BonusOffer, ...]:
        active = tuple(
            BonusOffer.objects.filter(
                user_id=self._user_id,
                valid_until__gt=timezone.now(),
                retired_at__isnull=True,
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
        ready = tuple(offer for offer in active if offer.is_preparation_ready)
        if ready:
            return ready
        review = next((offer for offer in active if offer.needs_review), None)
        if review is not None:
            raise _source_error(
                "bonus_offer_conditions_unsupported",
                review.unsupported_reason
                or "This promotion contains conditions that require manual review.",
            )
        raise _source_error(
            "bonus_offer_unavailable",
            "No active Bonus Offer is currently eligible for API-backed preparation.",
        )

    def _provider_state(self, provider_id: str) -> ProviderState:
        try:
            state = self._provider_state_repository.get(provider_id)
        except OSError:
            raise _source_error(
                "bonus_provider_state_unavailable",
                "Current sportsbook account/risk state is unavailable.",
            ) from None
        if state is None:
            raise _source_error(
                "bonus_provider_state_unavailable",
                "Current sportsbook account/risk state is unavailable.",
            )
        if state.provider_id != provider_id:
            raise _source_error(
                "bonus_provider_state_mismatch",
                "Current sportsbook account/risk state does not match the Bonus Offer.",
            )
        return state

    def _required_financial_profile(
        self,
        provider_id: str,
    ) -> SportsbookFinancialProfile:
        profile = self._optional_financial_profile(provider_id)
        if profile is None:
            raise _source_error(
                "bonus_financial_terms_missing",
                "Explicit sportsbook fee/tax terms are required for BonusEngine Simulation.",
            )
        return profile

    def _optional_financial_profile(
        self,
        provider_id: str,
    ) -> SportsbookFinancialProfile | None:
        try:
            return self._financial_profile_repository.get(provider_id)
        except ValueError:
            raise _source_error(
                "bonus_financial_terms_invalid",
                "Configured sportsbook fee/tax terms are invalid.",
            ) from None

    def _select_pair(
        self,
        snapshot: NormalizedMarketSnapshot,
        offer: BonusOffer,
        catalog: SportsbookCatalog,
        promotion_profile: SportsbookFinancialProfile,
    ) -> tuple[NormalizedOffer, NormalizedOffer, SportsbookFinancialProfile]:
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
        missing_financial_terms = False
        candidate_pairs: list[
            tuple[
                NormalizedOffer,
                NormalizedOffer,
                NormalizedOffer,
                NormalizedOffer,
                SportsbookFinancialProfile,
            ]
        ] = []
        for raw_promotion, promotion in promotion_candidates:
            for raw_hedge, hedge in canonical:
                if (
                    hedge.selection != promotion.selection
                    and hedge.provider != promotion.provider
                    and hedge.currency == promotion.currency
                ):
                    hedge_profile = self._optional_financial_profile(
                        hedge.provider
                    )
                    if hedge_profile is None:
                        missing_financial_terms = True
                    else:
                        candidate_pairs.append(
                            (
                                raw_promotion,
                                raw_hedge,
                                promotion,
                                hedge,
                                hedge_profile,
                            )
                        )
        if not candidate_pairs:
            if missing_financial_terms:
                raise _source_error(
                    "bonus_financial_terms_missing",
                    "Explicit fee/tax terms are missing for compatible opposing sportsbooks.",
                )
            raise _source_error(
                "bonus_market_no_compatible_offer",
                "No fresh opposing sportsbook offer is compatible with this Bonus Offer.",
            )
        selected = max(
            candidate_pairs,
            key=lambda pair: (
                pair[2].odds * (Decimal(1) - promotion_profile.fee_rate),
                pair[3].odds * (Decimal(1) - pair[4].fee_rate),
                pair[0].id,
                pair[1].id,
            ),
        )
        return selected[0], selected[1], selected[4]


def _build_bonus_inputs(
    offer: BonusOffer,
    promotion_offer: ArbitrageOffer,
    hedge_offer: ArbitrageOffer,
    promotion_profile: SportsbookFinancialProfile,
    hedge_profile: SportsbookFinancialProfile,
) -> tuple[
    SportsbookQualifyingBetInput | SportsbookFreeBetInput,
    BonusPreviewResult,
    str,
]:
    promotion_tax = SportsbookTaxTreatment(mode=promotion_profile.tax_mode)
    hedge_tax = SportsbookTaxTreatment(mode=hedge_profile.tax_mode)
    if offer.promotion_type == BonusOffer.PromotionType.QUALIFYING_BET:
        if offer.required_stake is None:
            raise _source_error(
                "bonus_offer_invalid",
                "The qualifying Bonus Offer is missing its required stake.",
            )
        qualifying = SportsbookQualifyingBetInput(
            promotion_offer=promotion_offer,
            hedge_offer=hedge_offer,
            promotion_tax=promotion_tax,
            hedge_tax=hedge_tax,
            qualifying_stake=offer.required_stake,
        )
        return (
            qualifying,
            calculate_sportsbook_qualifying_bet(qualifying),
            (
                "Bet & get · qualifying wager"
                if offer.effective_promotion_shape == BonusOffer.PromotionShape.BET_AND_GET
                else "Qualifying wager"
            ),
        )
    if offer.promotion_type == BonusOffer.PromotionType.FREE_BET:
        if offer.promotion_value is None or not offer.stake_return_rule:
            raise _source_error(
                "bonus_offer_invalid",
                "The free-bet Bonus Offer is missing required promotion terms.",
            )
        free_bet = SportsbookFreeBetInput(
            promotion_offer=promotion_offer,
            hedge_offer=hedge_offer,
            promotion_tax=promotion_tax,
            hedge_tax=hedge_tax,
            free_bet_amount=offer.promotion_value,
            stake_return_rule=FreeBetStakeReturn(offer.stake_return_rule),
        )
        return (
            free_bet,
            calculate_sportsbook_free_bet(free_bet),
            "Free bet already available",
        )
    raise _source_error(
        "bonus_offer_type_unsupported",
        "This Bonus Offer type is not supported by BonusEngine.",
    )


def _report_amounts(preview: BonusPreviewResult) -> tuple[CustomerReportAmount, ...]:
    if isinstance(preview, SportsbookQualifyingBetResult):
        amounts = [
            CustomerReportAmount(
                label="Promotion stake",
                amount=preview.promotion_stake,
            ),
            CustomerReportAmount(
                label="Hedge stake",
                amount=preview.hedge_stake,
            ),
        ]
    else:
        amounts = [
            CustomerReportAmount(
                label="Promotion amount",
                amount=preview.promotion_amount,
            ),
            CustomerReportAmount(
                label="Cash hedge stake",
                amount=preview.hedge_stake,
            ),
        ]
    if preview.upfront_tax_cost > 0:
        amounts.append(
            CustomerReportAmount(
                label="Upfront betting tax",
                amount=preview.upfront_tax_cost,
            )
        )
    return tuple(amounts)


def _report_financial_term(
    provider_name: str,
    profile: SportsbookFinancialProfile,
) -> CustomerReportFinancialTerm:
    tax = SportsbookTaxTreatment(mode=profile.tax_mode)
    return CustomerReportFinancialTerm(
        provider=provider_name,
        fee_rate=profile.fee_rate,
        tax_mode=profile.tax_mode.value,
        tax_rate=tax.rate,
    )


def _provider_display_name(catalog: SportsbookCatalog, provider_id: str) -> str:
    try:
        return next(
            provider.display_name
            for provider in catalog.providers
            if provider.provider_id == provider_id
        )
    except StopIteration:
        return provider_id


def _calculation_offer(
    offer: NormalizedOffer,
    stake_precision: Decimal,
    fee_rate: Decimal,
) -> ArbitrageOffer:
    return ArbitrageOffer(
        outcome=offer.selection,
        odds=offer.odds,
        available_liquidity=offer.available_stake,
        stake_precision=stake_precision,
        fee_rate=fee_rate,
        currency=offer.currency,
    )


def _source_error(reason_code: Identifier, message: str) -> SimulationOpportunitySourceError:
    return SimulationOpportunitySourceError(reason_code, message)
