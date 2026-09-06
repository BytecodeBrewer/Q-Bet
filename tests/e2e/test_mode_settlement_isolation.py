"""PostgreSQL-backed verification of mode routing and sandbox settlement isolation."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.test import TestCase

from qbet.calculations import ArbitrageOffer, QualifyingBetInput, TwoWayArbitrageInput
from qbet.domain.ledger import LedgerOperation, PortfolioBalance
from qbet.domain.verification import ProviderState
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.execution.models import ExecutionProposal, ExecutionRecord, Lifecycle, SandboxResult
from qbet.execution.sandbox import valuation
from qbet.execution.service import ExecutionService
from qbet.ledger import PortfolioLedger
from qbet.request_handler import (
    ExecutionSandboxRequestHandler,
    ModeRequestHandlers,
    RevalidationOutcome,
    SandboxRevalidationFixture,
    SimulationSandboxRequestHandler,
)
from qbet.settlement import SettlementService, ledger_command, transition
from qbet.simulation import (
    SimulationEngine,
    SimulationRunConfig,
    WorkflowSimulationRequest,
    WorkflowSimulationRunner,
)
from qbet.storage.ledger import ExecutionRecordRepository, PortfolioLedgerRepository
from qbet.storage.models import ExecutionRecordRow, PortfolioLedgerRow, SimulationReportRow
from qbet.storage.postgres import PostgresSimulationReportStore
from qbet.workflow import (
    WorkflowDecision,
    WorkflowMode,
    WorkflowOrchestrator,
    WorkflowRequest,
    WorkflowStage,
)
from qbet.workflow.routing import EngineModes, RoutingConfiguration, resolve_routes

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def _bonus_request() -> BonusEngineRequest:
    return BonusEngineRequest(
        opportunity_id="bonus-e2e",
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100"),
        ),
        currency="EUR",
        execution_offer_ids=("book", "exchange"),
        generated_at=NOW,
    )


def _sports_request() -> SportsCapitalEngineRequest:
    def offer(outcome: str) -> ArbitrageOffer:
        return ArbitrageOffer(
            outcome=outcome,
            odds=Decimal("2.2"),
            available_liquidity=Decimal("100"),
            stake_precision=Decimal("0.01"),
            currency="EUR",
        )

    return SportsCapitalEngineRequest(
        opportunity_id="sports-e2e",
        inputs=TwoWayArbitrageInput(
            first_offer=offer("home"),
            second_offer=offer("away"),
            requested_total_stake=Decimal("20"),
        ),
        currency="EUR",
        execution_offer_ids=("home", "away"),
        generated_at=NOW,
    )


def _handlers(opportunity_id: str, outcome: RevalidationOutcome = RevalidationOutcome.VALID):
    reason_code = None if outcome is RevalidationOutcome.VALID else "fixture_rejected"
    fixture = SandboxRevalidationFixture(
        opportunity_id=opportunity_id,
        outcome=outcome,
        validated_at=NOW,
        reason_code=reason_code,
    )
    return ModeRequestHandlers(
        simulation=SimulationSandboxRequestHandler(revalidation_fixtures=(fixture,)),
        execution=ExecutionSandboxRequestHandler(revalidation_fixtures=(fixture,)),
    )


def _execution_proposal(
    request: BonusEngineRequest | SportsCapitalEngineRequest, work
) -> ExecutionProposal:
    capital, payout = valuation(request)
    return ExecutionProposal(
        work=work,
        request=request,
        expires_at=NOW + timedelta(minutes=5),
        currency="EUR",
        capital_required=capital,
        payout=payout,
    )


def _execution_ledger() -> PortfolioLedger:
    return PortfolioLedger(
        balance=PortfolioBalance(mode="execution", currency="EUR", available=Decimal("1000"))
    )


class ModeSettlementIsolationE2ETests(TestCase):
    """Exercise public routing, workflow, persistence, execution, and settlement boundaries."""

    def _run_simulation(
        self,
        request: BonusEngineRequest | SportsCapitalEngineRequest,
        engine: SimulationEngine,
        *,
        correlation_id: UUID = CORRELATION_ID,
    ):
        store = PostgresSimulationReportStore()
        ledger_repository = PortfolioLedgerRepository()
        runner = WorkflowSimulationRunner(
            report_store=store,
            mode_request_handlers=_handlers(request.opportunity_id),
            ledger_writer=ledger_repository.save,
        )
        result = runner.run(
            WorkflowSimulationRequest(
                config=SimulationRunConfig(engine=engine, starting_capital=Decimal("100")),
                opportunities=(request,),
                provider_state=ProviderState(provider_id="book", active_bets_count=0),
                correlation_id=correlation_id,
            )
        )
        assert runner.last_report is not None
        assert runner.last_ledger is not None
        return result, runner.last_report, store, runner.last_ledger, ledger_repository

    def _settle_execution(self, request: BonusEngineRequest | SportsCapitalEngineRequest, work):
        ledger_repository = PortfolioLedgerRepository()
        record_repository = ExecutionRecordRepository()
        record, ledger = ExecutionService(
            ledger_writer=ledger_repository,
            execution_writer=record_repository,
        ).decide(
            ExecutionRecord(proposal=_execution_proposal(request, work)),
            _execution_ledger(),
            actor="owner",
            owner="owner",
            approve=True,
            now=NOW,
        )
        return record, ledger, ledger_repository, record_repository

    def test_simulation_only_bonus_persists_a_report_without_execution_state(self) -> None:
        request = _bonus_request()
        routes = resolve_routes(
            RoutingConfiguration(bonus=EngineModes(simulation=True)),
            "bonus",
            request.opportunity_id,
            CORRELATION_ID,
            "owner",
        )

        result, report, store, ledger, ledger_repository = self._run_simulation(
            request, SimulationEngine.BONUS
        )

        self.assertEqual(tuple(route.mode for route in routes), (WorkflowMode.SIMULATION,))
        evaluation = result.simulation_result.completed_steps[0].evaluation
        assert evaluation is not None
        self.assertEqual(evaluation.strategy_result.opportunity_id, request.opportunity_id)
        self.assertEqual(store.load_report(report.run_id), report)
        self.assertTrue(store.load_records(report.run_id))
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(ledger_repository.load(mode="simulation", currency="EUR"), ledger)
        self.assertEqual(ledger.balance.pending, Decimal(0))
        self.assertEqual(ledger.balance.reserved, Decimal(0))

    def test_execution_only_sports_settles_and_retrieves_its_own_persisted_state(self) -> None:
        request = _sports_request()
        (work,) = resolve_routes(
            RoutingConfiguration(sports_capital=EngineModes(execution=True)),
            "sports_capital",
            request.opportunity_id,
            CORRELATION_ID,
            "owner",
        )

        record, ledger, ledger_repository, record_repository = self._settle_execution(request, work)

        self.assertEqual(work.mode, WorkflowMode.EXECUTION)
        self.assertEqual(record.state, Lifecycle.SETTLED)
        self.assertIsNotNone(record.result)
        self.assertEqual(record_repository.load(work.id), record)
        self.assertEqual(ledger_repository.load(mode="execution", currency="EUR"), ledger)
        self.assertEqual(ledger.balance.reserved, Decimal(0))
        self.assertEqual(ledger.balance.locked, Decimal(0))
        self.assertEqual(ledger.balance.pending, Decimal(0))
        self.assertEqual(SimulationReportRow.objects.count(), 0)

    def test_dual_mode_creates_distinct_work_and_persisted_results(self) -> None:
        request = _sports_request()
        routes = resolve_routes(
            RoutingConfiguration(sports_capital=EngineModes(simulation=True, execution=True)),
            "sports_capital",
            request.opportunity_id,
            CORRELATION_ID,
            "owner",
        )
        simulation_work, execution_work = routes

        simulation, report, report_store, simulation_ledger, simulation_ledger_repository = (
            self._run_simulation(
                request,
                SimulationEngine.SPORTS_CAPITAL,
            )
        )
        execution, ledger, ledger_repository, record_repository = self._settle_execution(
            request,
            execution_work,
        )

        self.assertEqual(simulation_work.mode, WorkflowMode.SIMULATION)
        self.assertEqual(execution_work.mode, WorkflowMode.EXECUTION)
        self.assertNotEqual(simulation_work.id, execution_work.id)
        self.assertNotEqual(simulation_work.capital_context, execution_work.capital_context)
        self.assertEqual(simulation.correlation_id, execution.proposal.work.correlation_id)
        self.assertEqual(report_store.load_report(report.run_id), report)
        self.assertEqual(record_repository.load(execution_work.id), execution)
        self.assertEqual(ledger_repository.load(mode="execution", currency="EUR"), ledger)
        self.assertEqual(
            simulation_ledger_repository.load(mode="simulation", currency="EUR"), simulation_ledger
        )
        self.assertEqual(ExecutionRecordRow.objects.count(), 1)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 2)
        self.assertEqual(SimulationReportRow.objects.count(), 1)

    def test_repeated_settlement_delivery_is_idempotent(self) -> None:
        request = _sports_request()
        (work,) = resolve_routes(
            RoutingConfiguration(sports_capital=EngineModes(execution=True)),
            "sports_capital",
            request.opportunity_id,
            CORRELATION_ID,
            "owner",
        )
        record, ledger, _, _ = self._settle_execution(request, work)
        assert record.result is not None

        repeated_record, repeated_ledger = SettlementService().settle(record, ledger, record.result)

        self.assertEqual(repeated_record, record)
        self.assertEqual(repeated_ledger, ledger)
        self.assertEqual(ExecutionRecordRow.objects.count(), 1)

    def test_cancelled_execution_settlement_does_not_change_simulation_report(self) -> None:
        request = _bonus_request()
        routes = resolve_routes(
            RoutingConfiguration(bonus=EngineModes(simulation=True, execution=True)),
            "bonus",
            request.opportunity_id,
            CORRELATION_ID,
            "owner",
        )
        _, execution_work = routes
        simulation, report, report_store, _, _ = self._run_simulation(
            request, SimulationEngine.BONUS
        )
        record = ExecutionRecord(proposal=_execution_proposal(request, execution_work))
        ledger = _execution_ledger()
        for operation, state in (
            (LedgerOperation.RESERVE, Lifecycle.APPROVED),
            (LedgerOperation.LOCK, Lifecycle.DISPATCHED),
            (LedgerOperation.PENDING, Lifecycle.ACKNOWLEDGED),
        ):
            ledger, decision = ledger.apply(
                ledger_command(record, operation, record.proposal.capital_required)
            )
            self.assertTrue(decision.accepted)
            record = transition(record, state)
        cancelled = SandboxResult(
            dispatch_id=execution_work.id,
            correlation_id=CORRELATION_ID,
            mode="execution",
            currency="EUR",
            payout=record.proposal.payout,
            status="cancelled",
            observed_at=NOW,
        )

        cancelled_record, cancelled_ledger = SettlementService().settle(record, ledger, cancelled)

        self.assertEqual(cancelled_record.state, Lifecycle.CANCELLED)
        self.assertEqual(cancelled_ledger.balance.available, Decimal("1000"))
        self.assertEqual(report_store.load_report(report.run_id), report)
        self.assertTrue(simulation.simulation_result.completed_steps)

    def test_mode_revalidation_rejection_stops_dispatch_before_execution_state_exists(self) -> None:
        request = _sports_request()
        (work,) = resolve_routes(
            RoutingConfiguration(sports_capital=EngineModes(execution=True)),
            "sports_capital",
            request.opportunity_id,
            CORRELATION_ID,
            "owner",
        )
        result = WorkflowOrchestrator(
            mode_request_handlers=_handlers(request.opportunity_id, RevalidationOutcome.REJECTED)
        ).process(
            WorkflowRequest(
                id=str(work.id),
                opportunity_id=request.opportunity_id,
                mode=WorkflowMode.EXECUTION,
                correlation_id=CORRELATION_ID,
                stages=(WorkflowStage.LIQUIDITY_CHECK, WorkflowStage.DISPATCH),
            )
        )

        self.assertEqual(result.final_decision, WorkflowDecision.REJECT)
        self.assertIsNone(result.request_handler_result)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)
