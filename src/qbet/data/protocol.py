"""Provider-neutral contracts for permitted web and official API data sources."""

from __future__ import annotations

from typing import Protocol

from qbet.data.models import DataCollectionRequest, NormalizedMarketSnapshot


class DataCollector(Protocol):
    """A permitted collection implementation receives configuration at the app boundary."""

    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot: ...


class ApiAdapter(Protocol):
    """An official API implementation receives credentials outside normalized records."""

    def fetch(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot: ...
