from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from qbet.data.models import DataSourceMetadata, SourceTransport
from qbet.data.polling_work import PollingMarketSelection, PollingWorkItem, PollingWorkState

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=UTC)
SOURCE = DataSourceMetadata(
    provider_id="fixture",
    source_id="market",
    transport=SourceTransport.API,
)
SELECTION = PollingMarketSelection(
    sport="soccer_epl",
    event_id="event-207",
    market="h2h",
    event_starts_at=NOW + timedelta(hours=2),
)


def test_market_work_identity_is_deterministic_and_starts_without_fake_freshness() -> None:
    first = PollingWorkItem.create_market(
        owner="alice",
        source=SOURCE,
        engine="sports_capital",
        mode="simulation",
        selection=SELECTION,
        next_due_at=NOW,
    )
    repeated = PollingWorkItem.create_market(
        owner="alice",
        source=SOURCE,
        engine="sports_capital",
        mode="simulation",
        selection=SELECTION,
        next_due_at=NOW + timedelta(minutes=1),
    )

    assert first.id == repeated.id
    assert first.identity_key == repeated.identity_key
    assert first.request.fetched_at is None
    assert first.last_successful_fetch_at is None


def test_processing_state_requires_an_explicit_claim_timestamp() -> None:
    work = PollingWorkItem.create_market(
        owner="alice",
        source=SOURCE,
        engine="sports_capital",
        mode="simulation",
        selection=SELECTION,
        next_due_at=NOW,
    )
    payload = work.model_dump(mode="json")
    payload["state"] = PollingWorkState.PROCESSING.value

    with pytest.raises(ValidationError, match="requires claimed_at"):
        PollingWorkItem.model_validate(payload)
