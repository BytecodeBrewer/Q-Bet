"""Deterministic in-memory data source for tests and local workflow wiring."""

from __future__ import annotations

from qbet.data.models import DataCollectionRequest, NormalizedMarketSnapshot


class DeterministicInMemoryDataSource:
    def __init__(self, snapshots: tuple[NormalizedMarketSnapshot, ...]) -> None:
        self._snapshots = {snapshot.target: snapshot for snapshot in snapshots}
        if len(self._snapshots) != len(snapshots):
            raise ValueError("snapshots must have distinct targets")

    def collect(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot:
        return self._snapshot_for(request)

    def fetch(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot:
        return self._snapshot_for(request)

    def _snapshot_for(self, request: DataCollectionRequest) -> NormalizedMarketSnapshot:
        try:
            snapshot = self._snapshots[request.target]
        except KeyError as error:
            raise KeyError(f"no deterministic snapshot for {request.target.value}") from error
        if snapshot.source != request.source:
            raise ValueError("request source does not match deterministic snapshot source")
        return snapshot.model_copy(update={"correlation_id": request.correlation_id})
