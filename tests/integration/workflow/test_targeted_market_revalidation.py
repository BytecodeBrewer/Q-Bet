from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from qbet.data import (
    DataSourceMetadata,
    DataTarget,
    SourceTransport,
    TheOddsApiAdapter,
)
from qbet.domain.models import OfferSide
from qbet.request_handler import (
    ExecutionMarketRequestHandler,
    ExecutionSandboxRequestHandler,
    ExpectedMarketOffer,
    ModeRequestHandlers,
    RevalidationOutcome,
    SandboxRevalidationFixture,
    SimulationSandboxRequestHandler,
    TargetedMarketRevalidationContext,
    TheOddsApiTargetedMarketProvider,
)
from qbet.workflow import WorkflowDecision, WorkflowMode, WorkflowRequest, WorkflowStage
from qbet.workflow.orchestrator import WorkflowOrchestrator

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
SOURCE = DataSourceMetadata(
    provider_id="the_odds_api",
    source_id="primary-odds-feed",
    transport=SourceTransport.API,
)


def context(expected_odds: Decimal) -> TargetedMarketRevalidationContext:
    return TargetedMarketRevalidationContext(
        source=SOURCE,
        target=DataTarget.SPORTS_CAPITAL,
        sport="soccer_epl",
        event_id="event-123",
        market="h2h",
        expected_offers=(
            ExpectedMarketOffer(
                provider="book-one",
                selection="Home",
                side=OfferSide.BACK,
                odds=expected_odds,
            ),
        ),
        expires_at=NOW + timedelta(minutes=15),
    )


def payload(home_odds: Decimal) -> bytes:
    return json.dumps(
        {
            "id": "event-123",
            "sport_key": "soccer_epl",
            "bookmakers": [
                {
                    "key": "book-one",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Home", "price": str(home_odds)},
                                {"name": "Away", "price": "2.40"},
                            ],
                        }
                    ],
                }
            ],
        }
    ).encode()


def handlers(home_odds: Decimal) -> tuple[ModeRequestHandlers, list[str]]:
    requested_urls: list[str] = []
    adapter = TheOddsApiAdapter(
        api_key="configured-for-test",
        available_stake=Decimal("100"),
        clock=lambda: NOW,
        http_get=lambda url: (
            requested_urls.append(url) or 200,
            {},
            payload(home_odds),
        ),
    )
    execution = ExecutionMarketRequestHandler(
        provider=TheOddsApiTargetedMarketProvider(adapter),
        result_handler=ExecutionSandboxRequestHandler(),
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
    return ModeRequestHandlers(simulation=simulation, execution=execution), requested_urls


def workflow_request(mode: WorkflowMode, expected_odds: Decimal) -> WorkflowRequest:
    return WorkflowRequest(
        id="lifecycle-1",
        opportunity_id="opportunity-1",
        mode=mode,
        correlation_id=CORRELATION_ID,
        stages=(WorkflowStage.LIQUIDITY_CHECK, WorkflowStage.DISPATCH),
        market_revalidation=context(expected_odds),
    )


def test_execution_uses_one_exact_provider_refresh_and_allows_unchanged_market() -> None:
    mode_handlers, requested_urls = handlers(Decimal("2.25"))
    result = WorkflowOrchestrator(mode_request_handlers=mode_handlers).process(
        workflow_request(WorkflowMode.EXECUTION, Decimal("2.25"))
    )

    assert result.final_decision is WorkflowDecision.ALLOW
    assert len(requested_urls) == 1
    assert "/sports/soccer_epl/events/event-123/odds?" in requested_urls[0]
    assert "markets=h2h" in requested_urls[0]
    assert result.correlation_id == CORRELATION_ID


def test_changed_execution_quote_rechecks_before_dispatch_result_retrieval() -> None:
    mode_handlers, requested_urls = handlers(Decimal("2.30"))
    result = WorkflowOrchestrator(mode_request_handlers=mode_handlers).process(
        workflow_request(WorkflowMode.EXECUTION, Decimal("2.25"))
    )

    assert result.final_decision is WorkflowDecision.RECHECK
    assert len(requested_urls) == 1
    assert result.request_handler_result is None
    refresh = result.transitions[-1]
    assert refresh.stage is WorkflowStage.DISPATCH
    assert refresh.decision is WorkflowDecision.RECHECK
    assert refresh.reason == "market_offer_changed"


def test_simulation_keeps_deterministic_handler_and_makes_no_provider_request() -> None:
    mode_handlers, requested_urls = handlers(Decimal("2.25"))
    result = WorkflowOrchestrator(mode_request_handlers=mode_handlers).process(
        workflow_request(WorkflowMode.SIMULATION, Decimal("2.25"))
    )

    assert result.final_decision is WorkflowDecision.ALLOW
    assert requested_urls == []
