"""Normalized, transport-agnostic market-data contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from qbet.domain.models import (
    Currency,
    DomainModel,
    Identifier,
    NonNegativeDecimal,
    OfferSide,
)


class DataTarget(StrEnum):
    BONUS = "bonus"
    SPORTS_CAPITAL = "sports_capital"
    TICKET = "ticket"
    PREDICTION_MARKET = "prediction_market"
    CRYPTO_YIELD = "crypto_yield"


class SourceTransport(StrEnum):
    API = "api"
    WEB = "web"
    IN_MEMORY = "in_memory"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class CompletenessStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class OfferAvailability(StrEnum):
    AVAILABLE = "available"
    SUSPENDED = "suspended"


class DataSourceMetadata(DomainModel):
    """Provider metadata only; credentials belong to the application boundary."""

    provider_id: Identifier
    source_id: Identifier
    transport: SourceTransport


class NormalizedOffer(DomainModel):
    id: Identifier
    market_id: Identifier
    selection: Identifier
    provider: Identifier
    side: OfferSide
    odds: Decimal = Field(gt=Decimal(1), allow_inf_nan=False)
    available_stake: NonNegativeDecimal = Field(allow_inf_nan=False)
    currency: Currency
    availability: OfferAvailability = OfferAvailability.AVAILABLE
    observed_at: datetime
    source_updated_at: datetime | None = None
    stake_capacity_basis: str = "unspecified"
    account_availability_verified: bool = False
    canonical_provider_id: Identifier | None = None
    provider_mapping_status: str = "unknown"
    licensed_catalog_presence: bool = False

    @field_validator("observed_at", "source_updated_at")
    @classmethod
    def observed_at_is_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include timezone information")
        return value


class NormalizedMarketSnapshot(DomainModel):
    """Validated provider snapshot that may enter engine-specific preparation."""

    id: Identifier
    correlation_id: UUID
    target: DataTarget
    source: DataSourceMetadata
    sport: Identifier
    event_id: Identifier
    market_id: Identifier
    fetched_at: datetime
    freshness: FreshnessStatus
    completeness: CompletenessStatus
    offers: tuple[NormalizedOffer, ...] = Field(min_length=1)

    @field_validator("fetched_at")
    @classmethod
    def fetched_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fetched_at must include timezone information")
        return value

    @model_validator(mode="after")
    def offers_belong_to_snapshot_market(self) -> NormalizedMarketSnapshot:
        if any(offer.market_id != self.market_id for offer in self.offers):
            raise ValueError("offers must belong to the snapshot market_id")
        if len({offer.id for offer in self.offers}) != len(self.offers):
            raise ValueError("offers must have distinct identifiers")
        return self

    def require_ready_for_preparation(self) -> NormalizedMarketSnapshot:
        """Reject stale, partial, or suspended data before a preparation stage."""

        if self.freshness is not FreshnessStatus.FRESH:
            raise ValueError("market snapshot must be fresh for preparation")
        if self.completeness is not CompletenessStatus.COMPLETE:
            raise ValueError("market snapshot must be complete for preparation")
        if any(offer.availability is not OfferAvailability.AVAILABLE for offer in self.offers):
            raise ValueError("market snapshot contains unavailable offers")
        return self


class DataCollectionRequest(DomainModel):
    """A provider-neutral request for one selected remote market."""

    correlation_id: UUID
    target: DataTarget
    source: DataSourceMetadata
    sport: Identifier | None = None
    event_id: Identifier | None = None
    market: Identifier | None = None


class DiscoveredEvent(DomainModel):
    event_id: Identifier
    sport: Identifier
    starts_at: datetime

    @field_validator("starts_at")
    @classmethod
    def starts_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event start must include timezone information")
        return value
