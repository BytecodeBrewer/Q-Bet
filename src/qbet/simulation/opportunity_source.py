"""Simulation opportunity sources for deterministic fixtures and connected market data."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter
from typing import Protocol
from uuid import UUID

from pydantic import Field

from qbet.calculations import ArbitrageOffer, QualifyingBetInput, TwoWayArbitrageInput
from qbet.data import (
    DataCollectionRequest,
    DataSourceMetadata,
    DataTarget,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    SourceTransport,
    THE_ODDS_API_PROVIDER_ID,
    TheOddsApiAdapter,
    TheOddsApiAuthenticationError,
    TheOddsApiConfigurationError,
    TheOddsApiError,
    TheOddsApiPayloadError,
    TheOddsApiRateLimitError,
    TheOddsApiTransportError,
)
from qbet.data.sports_match_builder import (
    TwoWayArbitrageMatchMetadata,
    build_two_way_arbitrage_match,
)
from qbet.domain.models import DomainModel, Identifier, PositiveDecimal
from qbet.domain.verification import ProviderState
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.monitoring import MonitoringLevel, MonitoringRecord
from qbet.reporting import CustomerReportAmount, CustomerReportInput
from qbet.simulation.models import SimulationEngine, SimulationRunConfig

SimulationOpportunity = BonusEngineRequest | SportsCapitalEngineRequest


class SimulationMarketCollector(Protocol):
    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot: ...


class SimulationMonitoringWriter(Protocol):
    def append(self, record: MonitoringRecord) -> MonitoringRecord: ...


class SimulationOpportunitySource(Protocol):
    def build(
        self,
        config: SimulationRunConfig,
        correlation_id: UUID,
    ) -> "SimulationOpportunityBundle": ...


class SimulationOpportunitySourceError(RuntimeError):
    """Safe source failure with a stable application reason code."""

    def __init__(self, reason_code: Identifier, user_message: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.user_message = user_message


class TheOddsApiSportsSimulationConfig(DomainModel):
    sport: Identifier
    event_id: Identifier
    market: Identifier
    assumed_liquidity: PositiveDecimal
    requested_total_stake: PositiveDecimal
    stake_precision: PositiveDecimal
    first_fee_rate: Decimal = Field(
        default=Decimal(0), ge=Decimal(0), lt=Decimal(1), allow_inf_nan=False
    )
    second_fee_rate: Decimal = Field(
        default=Decimal(0), ge=Decimal(0), lt=Decimal(1), allow_inf_nan=False
    )


@dataclass(frozen=True)
class SimulationOpportunityBundle:
    opportunities: tuple[SimulationOpportunity, ...]
    provider_state: ProviderState
    customer_report_input: CustomerReportInput


class DeterministicSimulationOpportunitySource:
    """Keep deterministic offline/regression fixtures outside the connected product path."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def build(
        self,
        config: SimulationRunConfig,
        correlation_id: UUID,
    ) -> SimulationOpportunityBundle:
        generated_at = self._clock()
        if config.engine is SimulationEngine.BONUS:
            opportunities: tuple[SimulationOpportunity, ...] = tuple(
                BonusEngineRequest(
                    opportunity_id=f"gui-{correlation_id}-bonus-{index}",
                    inputs=QualifyingBetInput(
                        back_odds=Decimal("2.50"),
                        lay_odds=Decimal("2.60"),
                        back_stake=Decimal("10"),
                        exchange_commission=Decimal("0.02"),
                        stake_precision=Decimal("0.01"),
                        max_lay_liability=Decimal("1000"),
                    ),
                    currency="EUR",
                    execution_offer_ids=(
                        f"gui-book-{index}",
                        f"gui-exchange-{index}",
                    ),
                    generated_at=generated_at,
                )
                for index in range(1, 3)
            )
            report = CustomerReportInput(
                match="Legacy exchange-hedged bonus fixture",
                provider="Fixture sportsbook",
                counterparty_provider="Fixture exchange",
                strategy="Qualifying bet",
                assigned_amounts=(
                    CustomerReportAmount(label="Back stake", amount=Decimal("10")),
                    CustomerReportAmount(label="Lay stake", amount=Decimal("9.62")),
                ),
                invested_capital=Decimal("10"),
                currency="EUR",
            )
        elif config.engine is SimulationEngine.SPORTS_CAPITAL:

            def offer(outcome: str) -> ArbitrageOffer:
                return ArbitrageOffer(
                    outcome=outcome,
                    odds=Decimal("2.20"),
                    available_liquidity=Decimal("1000"),
                    stake_precision=Decimal("0.01"),
                    currency="EUR",
                )

            opportunities = tuple(
                SportsCapitalEngineRequest(
                    opportunity_id=f"gui-{correlation_id}-sports-{index}",
                    inputs=TwoWayArbitrageInput(
                        first_offer=offer(f"home-{index}"),
                        second_offer=offer(f"away-{index}"),
                        requested_total_stake=Decimal("20"),
                    ),
                    currency="EUR",
                    execution_offer_ids=(
                        f"gui-home-{index}",
                        f"gui-away-{index}",
                    ),
                    generated_at=generated_at,
                )
                for index in range(1, 3)
            )
            report = CustomerReportInput(
                match="Deterministic arbitrage fixture",
                provider="Fixture sportsbook A",
                counterparty_provider="Fixture sportsbook B",
                strategy="Two-way arbitrage",
                assigned_amounts=(
                    CustomerReportAmount(label="Home allocation", amount=Decimal("10")),
                    CustomerReportAmount(label="Away allocation", amount=Decimal("10")),
                ),
                invested_capital=Decimal("20"),
                currency="EUR",
            )
        else:
            raise SimulationOpportunitySourceError(
                "simulation_engine_unsupported",
                "Unsupported simulation engine.",
            )

        return SimulationOpportunityBundle(
            opportunities=opportunities,
            provider_state=ProviderState(
                provider_id="gui-sandbox-provider",
                active_bets_count=0,
            ),
            customer_report_input=report,
        )


class TheOddsApiSportsSimulationOpportunitySource:
    """Build one SportsCapital Simulation opportunity from one normalized provider snapshot."""

    def __init__(
        self,
        config: TheOddsApiSportsSimulationConfig,
        *,
        collector: SimulationMarketCollector | None = None,
        monitoring_writer: SimulationMonitoringWriter | None = None,
    ) -> None:
        self._config = config
        self._collector = collector or TheOddsApiAdapter(
            available_stake=config.assumed_liquidity
        )
        self._monitoring_writer = monitoring_writer

    def build(
        self,
        config: SimulationRunConfig,
        correlation_id: UUID,
    ) -> SimulationOpportunityBundle:
        if config.engine is not SimulationEngine.SPORTS_CAPITAL:
            raise SimulationOpportunitySourceError(
                "simulation_source_engine_mismatch",
                "Connected sports data is available only for SportsCapitalEngine.",
            )
        if self._config.requested_total_stake > self._config.assumed_liquidity:
            raise SimulationOpportunitySourceError(
                "simulation_liquidity_insufficient",
                "Configured Simulation liquidity is below the requested total stake.",
            )

        request = DataCollectionRequest(
            correlation_id=correlation_id,
            target=DataTarget.SPORTS_CAPITAL,
            source=DataSourceMetadata(
                provider_id=THE_ODDS_API_PROVIDER_ID,
                source_id="simulation-the-odds-api",
                transport=SourceTransport.API,
            ),
            sport=self._config.sport,
            event_id=self._config.event_id,
            market=self._config.market,
        )
        started = perf_counter()
        self._record_provider_activity(
            request,
            status="working",
            reason_code=None,
            level=MonitoringLevel.INFO,
        )
        try:
            snapshot = self._collector.collect(request)
        except TheOddsApiAuthenticationError:
            self._record_provider_activity(
                request,
                status="error",
                reason_code="simulation_odds_auth_failed",
                level=MonitoringLevel.ERROR,
                duration_ms=_elapsed_ms(started),
            )
            raise _source_error(
                "simulation_odds_auth_failed",
                "Connected sports data authentication failed.",
            ) from None
        except TheOddsApiRateLimitError:
            self._record_provider_activity(
                request,
                status="delayed",
                reason_code="simulation_odds_rate_limited",
                level=MonitoringLevel.WARNING,
                duration_ms=_elapsed_ms(started),
            )
            raise _source_error(
                "simulation_odds_rate_limited",
                "Connected sports data is temporarily rate limited.",
            ) from None
        except TheOddsApiConfigurationError:
            self._record_provider_activity(
                request,
                status="error",
                reason_code="simulation_odds_configuration_invalid",
                level=MonitoringLevel.ERROR,
                duration_ms=_elapsed_ms(started),
            )
            raise _source_error(
                "simulation_odds_configuration_invalid",
                "Connected sports data is not configured correctly.",
            ) from None
        except TheOddsApiPayloadError:
            self._record_provider_activity(
                request,
                status="error",
                reason_code="simulation_odds_invalid_payload",
                level=MonitoringLevel.ERROR,
                duration_ms=_elapsed_ms(started),
            )
            raise _source_error(
                "simulation_odds_invalid_payload",
                "Connected sports data could not be validated.",
            ) from None
        except TheOddsApiTransportError:
            self._record_provider_activity(
                request,
                status="unavailable",
                reason_code="simulation_odds_provider_unavailable",
                level=MonitoringLevel.ERROR,
                duration_ms=_elapsed_ms(started),
            )
            raise _source_error(
                "simulation_odds_provider_unavailable",
                "Connected sports data is temporarily unavailable.",
            ) from None
        except TheOddsApiError:
            self._record_provider_activity(
                request,
                status="unavailable",
                reason_code="simulation_odds_provider_unavailable",
                level=MonitoringLevel.ERROR,
                duration_ms=_elapsed_ms(started),
            )
            raise _source_error(
                "simulation_odds_provider_unavailable",
                "Connected sports data is temporarily unavailable.",
            ) from None
        self._record_provider_activity(
            request,
            status="success",
            reason_code=None,
            level=MonitoringLevel.INFO,
            duration_ms=_elapsed_ms(started),
        )

        first_offer, second_offer = _select_two_way_offers(
            snapshot,
            required_stake=self._config.requested_total_stake,
        )
        try:
            built = build_two_way_arbitrage_match(
                snapshot,
                TwoWayArbitrageMatchMetadata(
                    first_offer_id=first_offer.id,
                    second_offer_id=second_offer.id,
                    requested_total_stake=self._config.requested_total_stake,
                    first_stake_precision=self._config.stake_precision,
                    second_stake_precision=self._config.stake_precision,
                    first_fee_rate=self._config.first_fee_rate,
                    second_fee_rate=self._config.second_fee_rate,
                ),
            )
        except ValueError:
            raise _source_error(
                "simulation_market_preparation_failed",
                "Connected sports market could not be prepared for Simulation.",
            ) from None
        if built.context.correlation_id != correlation_id:
            raise _source_error(
                "simulation_market_identity_mismatch",
                "Connected sports market identity did not match the Simulation run.",
            )

        return SimulationOpportunityBundle(
            opportunities=(built.request,),
            provider_state=ProviderState(
                # Market-data source identity must not be reused as sportsbook/execution state.
                provider_id="gui-sandbox-provider",
                active_bets_count=0,
            ),
            customer_report_input=CustomerReportInput(
                match=f"{self._config.sport} / {self._config.event_id}",
                provider=first_offer.provider,
                counterparty_provider=second_offer.provider,
                strategy="Two-way arbitrage",
                assigned_amounts=(
                    CustomerReportAmount(
                        label="Requested total stake",
                        amount=self._config.requested_total_stake,
                    ),
                ),
                invested_capital=self._config.requested_total_stake,
                currency=built.request.currency,
                transaction_id=str(correlation_id),
            ),
        )


    def _record_provider_activity(
        self,
        request: DataCollectionRequest,
        *,
        status: str,
        reason_code: str | None,
        level: MonitoringLevel,
        duration_ms: int | None = None,
    ) -> None:
        if self._monitoring_writer is None:
            return
        try:
            self._monitoring_writer.append(
                MonitoringRecord(
                    correlation_id=request.correlation_id,
                    occurred_at=datetime.now(UTC),
                    engine=SimulationEngine.SPORTS_CAPITAL.value,
                    mode="simulation",
                    stage="data_aggregation",
                    event_type="provider_query",
                    status=status,
                    reason_code=reason_code,
                    level=level,
                    duration_ms=duration_ms,
                    references={
                        "provider_id": request.source.provider_id,
                        "source_id": request.source.source_id,
                    },
                )
            )
        except OSError:
            # Monitoring is observational and must never break Simulation.
            return


def _select_two_way_offers(
    snapshot: NormalizedMarketSnapshot,
    *,
    required_stake: Decimal,
) -> tuple[NormalizedOffer, NormalizedOffer]:
    try:
        snapshot.require_ready_for_preparation()
    except ValueError:
        raise _source_error(
            "simulation_market_not_ready",
            "Connected sports market is not ready for Simulation.",
        ) from None

    grouped: dict[str, list[NormalizedOffer]] = {}
    for offer in snapshot.offers:
        grouped.setdefault(offer.selection, []).append(offer)
    if len(grouped) != 2:
        raise _source_error(
            "simulation_market_requires_two_outcomes",
            "Connected SportsCapital Simulation requires exactly two distinct outcomes.",
        )

    selected = tuple(
        min(
            grouped[selection],
            key=lambda offer: (-offer.odds, offer.provider, offer.id),
        )
        for selection in sorted(grouped)
    )
    if any(offer.available_stake < required_stake for offer in selected):
        raise _source_error(
            "simulation_liquidity_insufficient",
            "Configured Simulation liquidity is below the requested total stake.",
        )
    return selected[0], selected[1]


def _source_error(
    reason_code: Identifier,
    user_message: str,
) -> SimulationOpportunitySourceError:
    return SimulationOpportunitySourceError(reason_code, user_message)

def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))
