"""Connected Phase 3 SportsCapital business-flow gate with offline provider transports."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from django.test import TransactionTestCase

from qbet.bank.balances import BankBalanceRequest, ReadOnlyBankBalanceService
from qbet.bank.bunq import (
    BunqAccountSnapshot,
    BunqBalanceProvider,
    BunqOperatingMode,
)
from qbet.bank.funding import FundingApprover
from qbet.data.models import DataSourceMetadata, DataTarget, SourceTransport
from qbet.data.results import ResultCollectionRequest, ResultProviderTarget
from qbet.data.the_odds_api import THE_ODDS_API_PROVIDER_ID, TheOddsApiAdapter
from qbet.data.the_odds_api_scores import TheOddsApiScoreCollector
from qbet.domain.models import OfferSide
from qbet.monitoring import MonitoringQuery, MonitoringService
from qbet.request_handler import (
    ExecutionMarketRequestHandler,
    ExecutionSandboxRequestHandler,
    ExpectedMarketOffer,
    ModeRequestHandlers,
    RevalidationOutcome,
    ResultStatus,
    SandboxResultFixture,
    SandboxRevalidationFixture,
    SimulationSandboxRequestHandler,
    TargetedMarketRevalidationContext,
    TheOddsApiTargetedMarketProvider,
)
from qbet.settlement.post_event import PostEventSettlementService
from qbet.simulation.models import SimulationEngine, SimulationRunConfig
from qbet.simulation.opportunity_source import (
    TheOddsApiSportsSimulationConfig,
    TheOddsApiSportsSimulationOpportunitySource,
)
from qbet.storage.ledger import (
    ExecutionStateRepository,
    ModeWorkQueueRepository,
    PortfolioLedgerRepository,
)
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.storage.postgres import PostgresSimulationReportReader
from qbet.workflow.approval import ExecutionApprovalService
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.funding import SimulationSandboxFundingCoordinator
from qbet.workflow.models import WorkflowMode
from qbet.workflow.queue import WorkState
from qbet.workflow.routing import EngineModes, RoutingConfiguration

NOW = datetime.now(UTC).replace(microsecond=0)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
SPORT = "soccer_epl"
EVENT_ID = "phase3-event-123"
MARKET = "h2h"
OWNER = "owner"
ODDS_KEY = "phase3-fake-odds-key"
SCORE_KEY = "phase3-fake-score-key"
RESULT_TIME = NOW + timedelta(minutes=6)
MARKET_SOURCE = DataSourceMetadata(
    provider_id=THE_ODDS_API_PROVIDER_ID,
    source_id="phase3-e2e-market",
    transport=SourceTransport.API,
)
BANK_SOURCE = DataSourceMetadata(
    provider_id="bunq",
    source_id="bunq_sandbox",
    transport=SourceTransport.API,
)


def _market_payload(*, home_best: str = "2.20", away_best: str = "2.20") -> bytes:
    return json.dumps(
        {
            "id": EVENT_ID,
            "sport_key": SPORT,
            "bookmakers": [
                {
                    "key": "book-a",
                    "markets": [
                        {
                            "key": MARKET,
                            "outcomes": [
                                {"name": "Home", "price": home_best},
                                {"name": "Away", "price": "1.80"},
                            ],
                        }
                    ],
                },
                {
                    "key": "book-b",
                    "markets": [
                        {
                            "key": MARKET,
                            "outcomes": [
                                {"name": "Home", "price": "1.80"},
                                {"name": "Away", "price": away_best},
                            ],
                        }
                    ],
                },
            ],
        }
    ).encode()


class RecordingHttp:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        return 200, {}, self.payload


class ScoreHttp:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, url: str):
        self.urls.append(url)
        return (
            200,
            {},
            json.dumps(
                [
                    {
                        "id": EVENT_ID,
                        "sport_key": SPORT,
                        "completed": True,
                        "last_update": RESULT_TIME.isoformat(),
                        "scores": [
                            {"name": "Home", "score": "2"},
                            {"name": "Away", "score": "1"},
                        ],
                    }
                ]
            ).encode(),
        )


class ExplodingHttp:
    def __call__(self, url: str):
        raise AssertionError("terminal result replay must not call the provider")


class BunqSandboxTransport:
    mode = BunqOperatingMode.SANDBOX

    def __init__(self) -> None:
        self.payment_calls = 0

    def read_account(self) -> BunqAccountSnapshot:
        return BunqAccountSnapshot(
            currency="EUR",
            available_balance=Decimal("500"),
            current_balance=Decimal("500"),
            observed_at=NOW + timedelta(minutes=1),
        )

    def create_sandbox_payment(
        self,
        *,
        amount: Decimal,
        currency: str,
        recipient_email: str,
        test_reference: str,
    ) -> str:
        assert amount == Decimal("0.01")
        assert currency == "EUR"
        assert recipient_email == "sandbox@example.invalid"
        assert test_reference.startswith("qbet-sandbox-")
        self.payment_calls += 1
        return "bunq-payment-0123456789ab"


class NoNotificationRecipients:
    def resolve(self):
        return ()


def _connected_request(correlation_id: UUID, market_http: RecordingHttp):
    adapter = TheOddsApiAdapter(
        api_key=ODDS_KEY,
        available_stake=Decimal("1000"),
        http_get=market_http,
        clock=lambda: NOW,
    )
    bundle = TheOddsApiSportsSimulationOpportunitySource(
        TheOddsApiSportsSimulationConfig(
            sport=SPORT,
            event_id=EVENT_ID,
            market=MARKET,
            assumed_liquidity=Decimal("1000"),
            requested_total_stake=Decimal("20"),
            stake_precision=Decimal("0.01"),
        ),
        collector=adapter,
    ).build(
        SimulationRunConfig(
            engine=SimulationEngine.SPORTS_CAPITAL,
            starting_capital=Decimal("100"),
        ),
        correlation_id,
    )
    return bundle.opportunities[0]


def _market_context() -> TargetedMarketRevalidationContext:
    return TargetedMarketRevalidationContext(
        source=MARKET_SOURCE,
        target=DataTarget.SPORTS_CAPITAL,
        sport=SPORT,
        event_id=EVENT_ID,
        market=MARKET,
        expected_offers=(
            ExpectedMarketOffer(
                provider="book-a",
                selection="Home",
                side=OfferSide.BACK,
                odds=Decimal("2.20"),
            ),
            ExpectedMarketOffer(
                provider="book-b",
                selection="Away",
                side=OfferSide.BACK,
                odds=Decimal("2.20"),
            ),
        ),
        expires_at=NOW + timedelta(minutes=25),
    )


def _mode_handlers(request, market_http: RecordingHttp) -> ModeRequestHandlers:
    result_fixture = SandboxResultFixture(
        opportunity_id=request.opportunity_id,
        status=ResultStatus.SUCCESS,
        observed_at=NOW + timedelta(minutes=1),
        result_reference="phase3-pre-dispatch-result",
    )
    simulation = SimulationSandboxRequestHandler(
        revalidation_fixtures=(
            SandboxRevalidationFixture(
                opportunity_id=request.opportunity_id,
                outcome=RevalidationOutcome.VALID,
                validated_at=NOW + timedelta(minutes=1),
            ),
        ),
        result_fixtures=(result_fixture,),
    )
    execution = ExecutionMarketRequestHandler(
        provider=TheOddsApiTargetedMarketProvider(
            TheOddsApiAdapter(
                api_key=ODDS_KEY,
                available_stake=Decimal("1000"),
                http_get=market_http,
                clock=lambda: NOW + timedelta(minutes=4),
            )
        ),
        result_handler=ExecutionSandboxRequestHandler(
            result_fixtures=(result_fixture,),
        ),
        clock=lambda: NOW + timedelta(minutes=4),
    )
    return ModeRequestHandlers(simulation=simulation, execution=execution)


def _assert_exact_market_urls(urls: list[str]) -> None:
    assert urls
    for url in urls:
        parsed = urlparse(url)
        assert parsed.path == f"/v4/sports/{SPORT}/events/{EVENT_ID}/odds"
        query = parse_qs(parsed.query)
        assert query["markets"] == [MARKET]


class ConnectedSportsCapitalPhase3E2ETests(TransactionTestCase):
    def test_connected_sports_capital_lifecycle_closes_external_boundaries_once(self) -> None:
        source_http = RecordingHttp(_market_payload())
        request = _connected_request(CORRELATION_ID, source_http)
        revalidation_http = RecordingHttp(_market_payload())
        queue_repository = ModeWorkQueueRepository()
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(
                sports_capital=EngineModes(
                    simulation=True,
                    execution=True,
                    execution_sandbox=True,
                )
            ),
            queue_repository=queue_repository,
            mode_request_handlers=_mode_handlers(request, revalidation_http),
            notification_recipient_resolver=NoNotificationRecipients(),
        )

        scheduled = coordinator.schedule(
            request,
            owner=OWNER,
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=30),
            market_revalidation=_market_context(),
        )
        self.assertEqual(
            {item.work.mode for item in scheduled},
            {WorkflowMode.SIMULATION, WorkflowMode.EXECUTION},
        )
        simulation_work = next(
            item for item in scheduled if item.work.mode is WorkflowMode.SIMULATION
        )
        execution_work = next(
            item for item in scheduled if item.work.mode is WorkflowMode.EXECUTION
        )
        self.assertNotEqual(simulation_work.work.id, execution_work.work.id)
        self.assertNotEqual(
            simulation_work.work.capital_context,
            execution_work.work.capital_context,
        )

        first_dispatch = coordinator.dispatch_due(
            now=NOW + timedelta(minutes=1),
            owner=OWNER,
        )
        first_by_mode = {item.work.mode: item for item in first_dispatch}
        self.assertEqual(first_by_mode[WorkflowMode.SIMULATION].state, WorkState.COMPLETED)
        self.assertEqual(first_by_mode[WorkflowMode.EXECUTION].state, WorkState.RECHECK)
        self.assertEqual(
            first_by_mode[WorkflowMode.EXECUTION].history[-1].reason,
            "execution_approval_required",
        )

        simulation_before_funding = PortfolioLedgerRepository().load(
            mode="simulation",
            currency="EUR",
        )
        self.assertIsNotNone(simulation_before_funding)
        assert simulation_before_funding is not None

        bunq = BunqSandboxTransport()
        balance = ReadOnlyBankBalanceService(
            BunqBalanceProvider(
                source=BANK_SOURCE,
                account_reference="bunq-***1234",
                transport=bunq,
            )
        ).read_balance(
            BankBalanceRequest(
                source=BANK_SOURCE,
                account_reference="bunq-***1234",
                currency="EUR",
                correlation_id=CORRELATION_ID,
                fresh_after=NOW,
            )
        ).require_fresh_balance()
        funding = SimulationSandboxFundingCoordinator(
            transport=bunq,
            recipient_email="sandbox@example.invalid",
            max_amount=Decimal("1"),
            max_balance_age=timedelta(minutes=5),
        )
        funding_approver = FundingApprover(identity=OWNER, is_authenticated=True)
        first_funding = funding.execute_for_completed_work(
            first_by_mode[WorkflowMode.SIMULATION],
            amount=Decimal("0.01"),
            balance=balance,
            approver=funding_approver,
            requested_at=NOW + timedelta(minutes=1),
            approved_at=NOW + timedelta(minutes=2),
            expires_at=NOW + timedelta(minutes=20),
        )
        restarted_funding = SimulationSandboxFundingCoordinator(
            transport=bunq,
            recipient_email="sandbox@example.invalid",
            max_amount=Decimal("1"),
            max_balance_age=timedelta(minutes=5),
        )
        repeated_funding = restarted_funding.execute_for_completed_work(
            first_by_mode[WorkflowMode.SIMULATION],
            amount=Decimal("0.01"),
            balance=balance,
            approver=funding_approver,
            requested_at=NOW + timedelta(minutes=1),
            approved_at=NOW + timedelta(minutes=2),
            expires_at=NOW + timedelta(minutes=20),
        )
        simulation_after_funding = PortfolioLedgerRepository().load(
            mode="simulation",
            currency="EUR",
        )
        self.assertTrue(first_funding.ledger_applied)
        self.assertTrue(repeated_funding.duplicate)
        self.assertEqual(bunq.payment_calls, 1)
        self.assertIsNotNone(simulation_after_funding)
        assert simulation_after_funding is not None
        self.assertEqual(
            simulation_after_funding.balance.available,
            simulation_before_funding.balance.available + Decimal("0.01"),
        )

        approved_execution = ExecutionApprovalService().decide(
            execution_work.work.id,
            actor=OWNER,
            approve=True,
            now=NOW + timedelta(minutes=3),
        )
        self.assertEqual(approved_execution.state.value, "approved")
        second_dispatch = coordinator.dispatch_due(
            now=NOW + timedelta(minutes=4),
            owner=OWNER,
        )
        self.assertEqual(len(second_dispatch), 1)
        self.assertEqual(second_dispatch[0].work.id, execution_work.work.id)
        self.assertEqual(second_dispatch[0].state, WorkState.RECHECK)
        self.assertEqual(second_dispatch[0].history[-1].reason, "post_event_result_pending")

        loaded = ExecutionStateRepository().load(execution_work.work.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        acknowledged_record, acknowledged_ledger = loaded
        self.assertEqual(acknowledged_record.state.value, "acknowledged")
        self.assertEqual(acknowledged_ledger.balance.mode, "execution")
        self.assertGreater(acknowledged_ledger.balance.pending, Decimal("0"))

        score_http = ScoreHttp()
        source = acknowledged_record.proposal.result_source
        target = acknowledged_record.proposal.result_provider_target
        self.assertIsNotNone(source)
        self.assertIsNotNone(target)
        assert source is not None and target is not None
        result_request = ResultCollectionRequest(
            match_id=acknowledged_record.proposal.work.opportunity_id,
            execution_id=str(acknowledged_record.proposal.work.id),
            correlation_id=CORRELATION_ID,
            source=source,
            fresh_after=NOW,
            provider_target=target,
        )
        settled = PostEventSettlementService(
            TheOddsApiScoreCollector(
                api_key=SCORE_KEY,
                http_get=score_http,
            )
        ).collect_and_settle(result_request)
        self.assertTrue(settled.settled)
        self.assertEqual(settled.record.state.value, "settled")
        self.assertEqual(len(score_http.urls), 1)
        score_url = urlparse(score_http.urls[0])
        self.assertEqual(score_url.path, f"/v4/sports/{SPORT}/scores/")
        self.assertEqual(parse_qs(score_url.query)["eventIds"], [EVENT_ID])

        replay = PostEventSettlementService(
            TheOddsApiScoreCollector(
                api_key=SCORE_KEY,
                http_get=ExplodingHttp(),
            )
        ).collect_and_settle(result_request)
        self.assertEqual(replay.record, settled.record)
        self.assertEqual(replay.ledger, settled.ledger)
        completed_queue = queue_repository.load(execution_work.work.id)
        self.assertIsNotNone(completed_queue)
        assert completed_queue is not None
        self.assertEqual(completed_queue.state, WorkState.COMPLETED)

        restored_execution = ExecutionStateRepository().load(execution_work.work.id)
        self.assertIsNotNone(restored_execution)
        assert restored_execution is not None
        restored_record, restored_execution_ledger = restored_execution
        self.assertEqual(restored_record, settled.record)
        self.assertEqual(restored_execution_ledger, settled.ledger)
        self.assertEqual(restored_execution_ledger.balance.mode, "execution")
        simulation_after_settlement = PortfolioLedgerRepository().load(
            mode="simulation",
            currency="EUR",
        )
        self.assertEqual(simulation_after_settlement, simulation_after_funding)

        report = PostgresSimulationReportReader().load_report(CORRELATION_ID)
        self.assertEqual(report.run_id, CORRELATION_ID)
        self.assertEqual(report.status.value, "completed")
        # The durable Reporting boundary can restore the completed connected Simulation.

        monitoring = MonitoringService(PostgresMonitoringRepository()).extended(
            MonitoringQuery(
                start=NOW - timedelta(minutes=1),
                end=RESULT_TIME + timedelta(minutes=1),
                correlation_id=CORRELATION_ID,
            )
        )
        self.assertTrue(monitoring.available)
        self.assertTrue(
            any(
                record.stage == "settlement"
                and record.event_type == "post_event_result"
                and record.status == "settled"
                for record in monitoring
            )
        )
        persisted_text = " ".join(
            record.model_dump_json() for record in monitoring
        ) + report.model_dump_json()
        self.assertNotIn(ODDS_KEY, persisted_text)
        self.assertNotIn(SCORE_KEY, persisted_text)
        _assert_exact_market_urls(source_http.urls)
        _assert_exact_market_urls(revalidation_http.urls)

    def test_changed_market_fails_closed_before_capital_or_result_side_effects(self) -> None:
        source_http = RecordingHttp(_market_payload())
        request = _connected_request(CORRELATION_ID, source_http)
        changed_http = RecordingHttp(_market_payload(home_best="2.10"))
        bunq = BunqSandboxTransport()
        score_http = ScoreHttp()
        queue_repository = ModeWorkQueueRepository()
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(
                sports_capital=EngineModes(
                    simulation=False,
                    execution=True,
                    execution_sandbox=True,
                )
            ),
            queue_repository=queue_repository,
            mode_request_handlers=_mode_handlers(request, changed_http),
            notification_recipient_resolver=NoNotificationRecipients(),
        )
        scheduled = coordinator.schedule(
            request,
            owner=OWNER,
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=30),
            market_revalidation=_market_context(),
        )
        self.assertEqual(len(scheduled), 1)

        dispatched = coordinator.dispatch_due(
            now=NOW + timedelta(minutes=1),
            owner=OWNER,
        )
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0].state, WorkState.RECHECK)
        self.assertEqual(dispatched[0].history[-1].reason, "revalidation_recheck")
        self.assertIsNone(ExecutionStateRepository().load(scheduled[0].work.id))

        rejected_balance = ReadOnlyBankBalanceService(
            BunqBalanceProvider(
                source=BANK_SOURCE,
                account_reference="bunq-***1234",
                transport=bunq,
            )
        ).read_balance(
            BankBalanceRequest(
                source=BANK_SOURCE,
                account_reference="bunq-***1234",
                currency="EUR",
                correlation_id=CORRELATION_ID,
                fresh_after=NOW,
            )
        ).require_fresh_balance()
        rejected_funding = SimulationSandboxFundingCoordinator(
            transport=bunq,
            recipient_email="sandbox@example.invalid",
            max_amount=Decimal("1"),
            max_balance_age=timedelta(minutes=5),
        )
        with self.assertRaisesMessage(ValueError, "simulation_funding_work_not_completed"):
            rejected_funding.execute_for_completed_work(
                dispatched[0],
                amount=Decimal("0.01"),
                balance=rejected_balance,
                approver=FundingApprover(identity=OWNER, is_authenticated=True),
                requested_at=NOW + timedelta(minutes=1),
                approved_at=NOW + timedelta(minutes=2),
                expires_at=NOW + timedelta(minutes=20),
            )

        result_request = ResultCollectionRequest(
            match_id=request.opportunity_id,
            execution_id=str(scheduled[0].work.id),
            correlation_id=CORRELATION_ID,
            source=MARKET_SOURCE,
            fresh_after=NOW,
            provider_target=ResultProviderTarget(
                sport=SPORT,
                event_id=EVENT_ID,
            ),
        )
        # A non-trackable execution is rejected before the real score collector can call HTTP.
        with self.assertRaisesMessage(ValueError, "result_execution_state_not_found"):
            PostEventSettlementService(
                TheOddsApiScoreCollector(
                    api_key=SCORE_KEY,
                    http_get=score_http,
                )
            ).collect_and_settle(result_request)

        self.assertEqual(score_http.urls, [])
        self.assertEqual(bunq.payment_calls, 0)
        self.assertIsNone(
            PortfolioLedgerRepository().load(mode="execution", currency="EUR")
        )
        monitoring = MonitoringService(PostgresMonitoringRepository()).extended(
            MonitoringQuery(
                start=NOW - timedelta(minutes=1),
                end=NOW + timedelta(minutes=2),
                correlation_id=CORRELATION_ID,
            )
        )
        self.assertTrue(
            any(record.reason_code == "market_offer_changed" for record in monitoring)
        )
