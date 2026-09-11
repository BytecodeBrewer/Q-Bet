"""Normalized data-aggregation contracts."""

from .fakes import DeterministicInMemoryDataSource
from .models import (
    CompletenessStatus,
    DataCollectionRequest,
    DataSourceMetadata,
    DataTarget,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    OfferAvailability,
    SourceTransport,
)
from .protocol import ApiAdapter, DataCollector
from .the_odds_api import (
    THE_ODDS_API_PROVIDER_ID,
    TheOddsApiAdapter,
    TheOddsApiConfigurationError,
    TheOddsApiError,
    TheOddsApiPayloadError,
)

__all__ = [
    "ApiAdapter",
    "CompletenessStatus",
    "DataCollectionRequest",
    "DataCollector",
    "DataSourceMetadata",
    "DataTarget",
    "DeterministicInMemoryDataSource",
    "FreshnessStatus",
    "NormalizedMarketSnapshot",
    "NormalizedOffer",
    "OfferAvailability",
    "SourceTransport",
    "THE_ODDS_API_PROVIDER_ID",
    "TheOddsApiAdapter",
    "TheOddsApiConfigurationError",
    "TheOddsApiError",
    "TheOddsApiPayloadError",
]
