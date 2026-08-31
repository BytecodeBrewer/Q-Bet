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
]
