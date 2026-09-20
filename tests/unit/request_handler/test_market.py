from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from qbet.data import (
    CompletenessStatus,
    DataSourceMetadata,
    DataTarget,
    FreshnessStatus,
    NormalizedMarketSnapshot,
    NormalizedOffer,
    OfferAvailability,
    SourceTransport,
)
from qbet.domain.models import OfferSide
from qbet.request_handler import (
    ExecutionMarketRequestHandler,
    ExecutionSandboxRequestHandler,
    ExpectedMarketOffer,
    ModeRequest,
    ModeRequestHandlers,
    RequestHandlerMode,
    ResultStatus,
    RevalidationOutcome,
    SandboxResultFixture,
    SandboxRevalidationFixture,
    SimulationSandboxRequestHandler,
    TargetedMarketProviderError,
    TargetedMarketRevalidationContext,
)

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
SOURCE = DataSourceMetadata(
    provider_id="the_odds_api",
    source_id="primary-odds-feed",
    transport=SourceTransport.API,
)


class RecordingProvider:
    def __init__(
        self,
        snapshot: NormalizedMarketSnapshot | None = None,
        error: TargetedMarketProviderError | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.error = error
        self.requests = []

    def refresh(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.snapshot is not None
        return self.snapshot


def market_context(**changes: object) -> TargetedMarketRevalidationContext:
    values: dict[str, object] = {
        "source": SOURCE,
        "target": DataTarget.SPORTS_CAPITAL,
        "sport": "soccer_epl",
        "event_id": "event-123",
        "market": "h2h",
        "expected_offers": (
            ExpectedMarketOffer(
                provider="book-one",
                selection="Home",
                side=OfferSide.BACK,
                odds=Decimal("2.25"),
            ),
        ),
        "expires_at": NOW + timedelta(minutes=15),
    }
    values.update(changes)
    return TargetedMarketRevalidationContext.model_validate(values)


def mode_request(
    mode: RequestHandlerMode = RequestHandlerMode.EXECUTION,
    *,
    context: TargetedMarketRevalidationContext | None = None,
) -> ModeRequest:
    return ModeRequest(
        opportunity_id="opportunity-1",
        mode=mode,
        correlation_id=CORRELATION_ID,
        lifecycle_id="lifecycle-1",
        market_revalidation=context or market_context(),
    )


def snapshot(
    *,
    odds: Decimal = Decimal("2.25"),
    availability: OfferAvailability = OfferAvailability.AVAILABLE,
) -> NormalizedMarketSnapshot:
    return NormalizedMarketSnapshot(
        id="event-123:h2h",
        correlation_id=CORRELATION_ID,
        target=DataTarget.SPORTS_CAPITAL,
        source=SOURCE,
        sport="soccer_epl",
        event_id="event-123",
        market_id="event-123:h2h",
        fetched_at=NOW,
        freshness=FreshnessStatus.FRESH,
        completeness=CompletenessStatus.COMPLETE,
        offers=(
            NormalizedOffer(
                id="book-one:Home",
                market_id="event-123:h2h",
                selection="Home",
                provider="book-one",
                side=OfferSide.BACK,
                odds=odds,
                available_stake=Decimal("100"),
                currency="EUR",
                availability=availability,
                observed_at=NOW,
            ),
        ),
    )


def result_delegate() -> ExecutionSandboxRequestHandler:
    return ExecutionSandboxRequestHandler(
        result_fixtures=(
            SandboxResultFixture(
                opportunity_id="opportunity-1",
                status=ResultStatus.SUCCESS,
                observed_at=NOW,
                result_reference="sandbox-result",
            ),
        )
    )


def test_matching_targeted_snapshot_is_valid_and_preserves_identity() -> None:
    provider = RecordingProvider(snapshot())
    handler = ExecutionMarketRequestHandler(
        provider=provider,
        result_handler=result_delegate(),
        clock=lambda: NOW,
    )

    result = handler.revalidate(mode_request())

    assert result.outcome is RevalidationOutcome.VALID
    assert result.reason_code is None
    assert result.correlation_id == CORRELATION_ID
    assert result.opportunity_id == "opportunity-1"
    assert result.lifecycle_id == "lifecycle-1"
    assert len(provider.requests) == 1
    provider_request = provider.requests[0]
    assert provider_request.event_id == "event-123"
    assert provider_request.market == "h2h"
    assert provider_request.correlation_id == CORRELATION_ID


def test_material_quote_change_requires_recheck() -> None:
    handler = ExecutionMarketRequestHandler(
        provider=RecordingProvider(snapshot(odds=Decimal("2.30"))),
        result_handler=result_delegate(),
        clock=lambda: NOW,
    )

    result = handler.revalidate(mode_request())

    assert result.outcome is RevalidationOutcome.CHANGED
    assert result.reason_code == "market_offer_changed"
    assert ModeRequestHandlers.workflow_decision(result) == ("recheck", "market_offer_changed")


def test_unavailable_offer_is_rejected() -> None:
    handler = ExecutionMarketRequestHandler(
        provider=RecordingProvider(snapshot(availability=OfferAvailability.SUSPENDED)),
        result_handler=result_delegate(),
        clock=lambda: NOW,
    )

    result = handler.revalidate(mode_request())

    assert result.outcome is RevalidationOutcome.REJECTED
    assert result.reason_code == "market_offer_unavailable"
    assert ModeRequestHandlers.workflow_decision(result) == ("reject", "market_offer_unavailable")


def test_expired_opportunity_is_rejected_without_provider_call() -> None:
    provider = RecordingProvider(snapshot())
    handler = ExecutionMarketRequestHandler(
        provider=provider,
        result_handler=result_delegate(),
        clock=lambda: NOW,
    )
    expired = market_context(expires_at=NOW - timedelta(seconds=1))

    result = handler.revalidate(mode_request(context=expired))

    assert result.outcome is RevalidationOutcome.EXPIRED
    assert result.reason_code == "market_opportunity_expired"
    assert provider.requests == []


@pytest.mark.parametrize(
    "reason_code",
    [
        "market_provider_configuration_failed",
        "market_provider_auth_failed",
        "market_provider_rate_limited",
        "market_provider_unavailable",
        "market_provider_invalid_payload",
    ],
)
def test_provider_failures_become_safe_unavailable_results(reason_code: str) -> None:
    handler = ExecutionMarketRequestHandler(
        provider=RecordingProvider(error=TargetedMarketProviderError(reason_code)),
        result_handler=result_delegate(),
        clock=lambda: NOW,
    )

    result = handler.revalidate(mode_request())

    assert result.outcome is RevalidationOutcome.UNAVAILABLE
    assert result.reason_code == reason_code


def test_result_retrieval_remains_delegated_to_existing_handler() -> None:
    handler = ExecutionMarketRequestHandler(
        provider=RecordingProvider(snapshot()),
        result_handler=result_delegate(),
        clock=lambda: NOW,
    )

    result = handler.retrieve_result(mode_request())

    assert result.status is ResultStatus.SUCCESS
    assert result.result_reference == "sandbox-result"
    assert result.correlation_id == CORRELATION_ID


def test_simulation_route_never_calls_execution_market_provider() -> None:
    provider = RecordingProvider(snapshot())
    execution = ExecutionMarketRequestHandler(
        provider=provider,
        result_handler=result_delegate(),
        clock=lambda: NOW,
    )
    simulation = SimulationSandboxRequestHandler(
        revalidation_fixtures=(
            SandboxRevalidationFixture(
                opportunity_id="opportunity-1",
                outcome=RevalidationOutcome.VALID,
                validated_at=NOW,
            ),
        )
    )
    handlers = ModeRequestHandlers(simulation=simulation, execution=execution)

    result = handlers.revalidate(mode_request(RequestHandlerMode.SIMULATION))

    assert result.outcome is RevalidationOutcome.VALID
    assert provider.requests == []
