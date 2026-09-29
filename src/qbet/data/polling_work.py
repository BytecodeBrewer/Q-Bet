"""Typed durable work state for hosted Smart Polling."""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, model_validator

from qbet.data.models import DataSourceMetadata, NormalizedMarketSnapshot
from qbet.data.polling import PollingRequest, PollingTarget
from qbet.domain.models import DomainModel, Identifier


class PollingWorkState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    TERMINAL = "terminal"
    DISABLED = "disabled"


class PollingMarketSelection(DomainModel):
    """Configured provider-neutral identity for one market polling target."""

    sport: Identifier
    event_id: Identifier
    market: Identifier
    event_starts_at: AwareDatetime


class PollingWorkItem(DomainModel):
    """Restart-safe polling work plus the latest validated normalized snapshot."""

    id: UUID
    identity_key: Identifier
    owner: Identifier
    request: PollingRequest
    selection: PollingMarketSelection
    state: PollingWorkState = PollingWorkState.PENDING
    next_due_at: AwareDatetime
    last_outcome: Identifier | None = None
    last_reason: Identifier | None = None
    last_successful_fetch_at: AwareDatetime | None = None
    snapshot: NormalizedMarketSnapshot | None = None
    claimed_at: AwareDatetime | None = None

    @classmethod
    def create_market(
        cls,
        *,
        owner: Identifier,
        source: DataSourceMetadata,
        engine: Identifier,
        mode: Identifier,
        selection: PollingMarketSelection,
        next_due_at: AwareDatetime,
    ) -> "PollingWorkItem":
        identity_key = polling_work_identity(
            owner=owner,
            source=source,
            engine=engine,
            mode=mode,
            selection=selection,
        )
        work_id = uuid5(NAMESPACE_URL, f"qbet:polling:{identity_key}")
        correlation_id = uuid5(work_id, "correlation")
        return cls(
            id=work_id,
            identity_key=identity_key,
            owner=owner,
            request=PollingRequest(
                source=source,
                target=PollingTarget.MARKET,
                match_id=selection.event_id,
                correlation_id=correlation_id,
                fetched_at=None,
                event_starts_at=selection.event_starts_at,
                next_poll_at=next_due_at,
                attempt=0,
                mode=mode,
                engine=engine,
            ),
            selection=selection,
            next_due_at=next_due_at,
        )

    @model_validator(mode="after")
    def validates_state_and_snapshot(self) -> "PollingWorkItem":
        if self.state is PollingWorkState.PROCESSING and self.claimed_at is None:
            raise ValueError("processing polling work requires claimed_at")
        if self.state is not PollingWorkState.PROCESSING and self.claimed_at is not None:
            raise ValueError("only processing polling work may retain claimed_at")
        if self.request.match_id != self.selection.event_id:
            raise ValueError("polling work selection must match request identity")
        if self.request.event_starts_at != self.selection.event_starts_at:
            raise ValueError("polling work event timing must match request timing")
        if self.last_successful_fetch_at != self.request.fetched_at:
            raise ValueError("polling work successful fetch time must match request freshness")
        if self.snapshot is not None:
            if self.snapshot.correlation_id != self.request.correlation_id:
                raise ValueError("polling work snapshot correlation must match request")
            if self.snapshot.event_id != self.selection.event_id:
                raise ValueError("polling work snapshot event must match selection")
        return self


def polling_work_identity(
    *,
    owner: str,
    source: DataSourceMetadata,
    engine: str,
    mode: str,
    selection: PollingMarketSelection,
) -> str:
    """Hash non-secret route/target identity into a compact unique database key."""

    raw = "\x1f".join(
        (
            owner,
            source.provider_id,
            source.source_id,
            PollingTarget.MARKET.value,
            engine,
            mode,
            selection.sport,
            selection.event_id,
            selection.market,
        )
    )
    return sha256(raw.encode("utf-8")).hexdigest()
