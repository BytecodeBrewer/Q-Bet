"""PostgreSQL integration coverage for queue control and mode isolation."""

import asyncio
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from decimal import Decimal
from uuid import UUID

from django.db import close_old_connections
from django.test import TransactionTestCase

from qbet.calculations import ArbitrageOffer, TwoWayArbitrageInput
from qbet.domain.ledger import LedgerOperation, PortfolioBalance
from qbet.domain.verification import ProviderState
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.execution.models import ExecutionProposal, ExecutionRecord, Lifecycle, SandboxResult
from qbet.execution.sandbox import valuation
from qbet.ledger import PortfolioLedger
from qbet.orchestrator import RejectionReason
from qbet.request_handler import ResultStatus, RevalidationOutcome
from qbet.settlement import SettlementService, ledger_command, transition
from qbet.simulation import (
    SimulationEngine,
    SimulationRunConfig,
    WorkflowSimulationRequest,
    WorkflowSimulationRunner,
)
from qbet.storage.ledger import (
    ModeWorkQueueRepository,
    PortfolioLedgerRepository,
)
from qbet.storage.models import (
    ExecutionRecordRow,
    ModeWorkQueueRow,
    PortfolioLedgerRow,
    SimulationReportRow,
)
from qbet.storage.postgres import PostgresSimulationReportStore
from qbet.storage.simulation_ledger import SimulationPortfolioLedgerRepository
from qbet.workflow import WorkflowDecision, WorkflowStage, WorkState
from qbet.workflow.approval import ExecutionApprovalService
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.routing import EngineModes, RoutingConfiguration, resolve_routes
from qbet.web.simulation_control import SimulationControlService
from tests.support.workflow import bonus_request, sandbox_mode_handlers

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def _bonus_request() -> BonusEngineRequest:
    return bonus_request("bonus-e2e", generated_at=NOW)


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


def _handlers(
    opportunity_id: str,
    outcome: RevalidationOutcome = RevalidationOutcome.VALID,
    result_status: ResultStatus = ResultStatus.SUCCESS,
):
    return sandbox_mode_handlers(
        opportunity_id,
        observed_at=NOW,
        simulation_outcome=outcome,
        execution_outcome=outcome,
        result_status=result_status,
        simulation_reason_code="fixture_rejected",
        execution_reason_code="fixture_rejected",
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


class ModeQueueAndIsolationIntegrationTests(TransactionTestCase):
    """Own queue timing, claiming, result-state, and cross-mode isolation regressions."""

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

    def _coordinator(self, configuration: RoutingConfiguration, opportunity_id: str):
        return ModeDispatchCoordinator(
            configuration,
            queue_repository=ModeWorkQueueRepository(),
            mode_request_handlers=_handlers(opportunity_id),
        )

    def test_concurrent_engines_resolve_shared_capital_at_liquidity_gate(self) -> None:
        SimulationControlService().seed_portfolio(amount=Decimal("20"), currency="EUR")
        repository = SimulationPortfolioLedgerRepository()
        bonus_snapshot = repository.load(currency="EUR")
        sports_snapshot = repository.load(currency="EUR")
        assert bonus_snapshot is not None and sports_snapshot is not None

        bonus = bonus_request(
            "bonus-concurrent-capital",
            generated_at=NOW,
            back_stake=Decimal("20"),
            max_lay_liability=Decimal("1000"),
        )
        sports = _sports_request().model_copy(
            update={"opportunity_id": "sports-concurrent-capital"}
        )
        reservation_held = Event()
        release_first = Event()
        first_errors: list[BaseException] = []
        first_results = []

        def hold_after_first_post_reserve_merge(incoming: PortfolioLedger) -> PortfolioLedger:
            persisted = repository.merge(incoming)
            if not reservation_held.is_set():
                reservation_held.set()
                if not release_first.wait(timeout=10):
                    raise TimeoutError("test did not release first Simulation reservation")
            return persisted

        first_runner = WorkflowSimulationRunner(
            simulation_ledger=bonus_snapshot,
            ledger_writer=hold_after_first_post_reserve_merge,
            liquidity_reserver=repository.reserve_with_liquidity,
        )
        second_runner = WorkflowSimulationRunner(
            simulation_ledger=sports_snapshot,
            ledger_writer=repository.merge,
            liquidity_reserver=repository.reserve_with_liquidity,
        )

        def run_first() -> None:
            close_old_connections()
            try:
                first_results.append(
                    first_runner.run(
                        WorkflowSimulationRequest(
                            config=SimulationRunConfig(
                                engine=SimulationEngine.BONUS,
                                starting_capital=Decimal("20"),
                            ),
                            opportunities=(bonus,),
                            provider_state=ProviderState(
                                provider_id="book",
                                active_bets_count=0,
                            ),
                        )
                    )
                )
            except BaseException as error:
                first_errors.append(error)
            finally:
                close_old_connections()

        worker = Thread(target=run_first)
        worker.start()
        self.assertTrue(reservation_held.wait(timeout=10))

        second = second_runner.run(
            WorkflowSimulationRequest(
                config=SimulationRunConfig(
                    engine=SimulationEngine.SPORTS_CAPITAL,
                    starting_capital=Decimal("20"),
                ),
                opportunities=(sports,),
                provider_state=ProviderState(
                    provider_id="book",
                    active_bets_count=0,
                ),
            )
        )

        self.assertEqual(second.simulation_result.completed_steps, ())
        self.assertEqual(
            second.workflow_results[0].final_decision,
            WorkflowDecision.REJECT,
        )
        liquidity = next(
            transition
            for transition in second.workflow_results[0].transitions
            if transition.stage is WorkflowStage.LIQUIDITY_CHECK
        )
        self.assertEqual(liquidity.reason, RejectionReason.CAPITAL_LIMIT.value)
        self.assertFalse(
            any(
                command.correlation_id == str(second.correlation_id)
                for command in (
                    repository.load(currency="EUR").commands.values()
                    if repository.load(currency="EUR") is not None
                    else ()
                )
            )
        )

        release_first.set()
        worker.join(timeout=15)

        self.assertFalse(worker.is_alive())
        self.assertEqual(first_errors, [])
        self.assertEqual(len(first_results), 1)
        self.assertEqual(
            first_results[0].workflow_results[0].final_decision,
            WorkflowDecision.ALLOW,
        )
        persisted = repository.load(currency="EUR")
        assert persisted is not None
        correlations = {command.correlation_id for command in persisted.commands.values()}
        self.assertIn(str(first_results[0].correlation_id), correlations)
        self.assertNotIn(str(second.correlation_id), correlations)

    def test_queue_recheck_and_expiry_are_persisted_without_dispatch(self) -> None:
        request = _sports_request()
        recheck = ModeDispatchCoordinator(
            RoutingConfiguration(sports_capital=EngineModes(execution=True)),
            queue_repository=ModeWorkQueueRepository(),
            mode_request_handlers=_handlers(request.opportunity_id, RevalidationOutcome.CHANGED),
        )
        (recheck_item,) = recheck.schedule(
            request,
            owner="owner",
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        (rechecked,) = recheck.dispatch_due(now=NOW, owner="owner")

        self.assertEqual(rechecked.state, WorkState.RECHECK)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        expired = self._coordinator(
            RoutingConfiguration(sports_capital=EngineModes(simulation=True)), request.opportunity_id
        )
        (expired_item,) = expired.schedule(
            request.model_copy(update={"opportunity_id": "sports-expired"}),
            owner="owner",
            correlation_id=UUID("87654321-4321-8765-4321-876543218765"),
            scheduled_for=NOW - timedelta(minutes=2),
            expires_at=NOW - timedelta(minutes=1),
        )
        expired_result = next(
            item
            for item in expired.dispatch_due(now=NOW, owner="owner")
            if item.work.id == expired_item.work.id
        )

        self.assertEqual(recheck_item.work.id, rechecked.work.id)
        self.assertEqual(expired_item.work.id, expired_result.work.id)
        self.assertEqual(expired_result.state, WorkState.EXPIRED)
        self.assertEqual(ModeWorkQueueRow.objects.count(), 2)
    def test_atomic_claim_prevents_a_second_worker_from_dispatching_the_same_item(self) -> None:
        request = _sports_request()
        configuration = RoutingConfiguration(sports_capital=EngineModes(execution=True))
        first_worker = self._coordinator(configuration, request.opportunity_id)
        second_worker = self._coordinator(configuration, request.opportunity_id)
        (scheduled,) = first_worker.schedule(
            request,
            owner="owner",
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        first_result = first_worker.dispatch_due(now=NOW, owner="owner")
        second_result = second_worker.dispatch_due(now=NOW, owner="owner")

        self.assertEqual(tuple(item.state for item in first_result), (WorkState.RECHECK,))
        self.assertEqual(second_result, ())
        self.assertEqual(ExecutionRecordRow.objects.count(), 1)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 1)

        ExecutionApprovalService().decide(
            scheduled.work.id,
            actor="owner",
            approve=True,
            now=NOW + timedelta(seconds=1),
        )
        completed = second_worker.dispatch_due(
            now=NOW + timedelta(seconds=2), owner="owner"
        )
        self.assertEqual(tuple(item.state for item in completed), (WorkState.COMPLETED,))

    def test_async_wait_reschedule_dispatches_once_at_the_controlled_time(self) -> None:
        SimulationControlService().seed_portfolio(amount=Decimal("100"), currency="EUR")
        request = _bonus_request()
        coordinator = self._coordinator(
            RoutingConfiguration(bonus=EngineModes(simulation=True)), request.opportunity_id
        )
        scheduled_for = NOW + timedelta(seconds=30)
        (item,) = coordinator.schedule(
            request,
            owner="owner",
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )
        ModeWorkQueueRepository().reschedule(
            item.work.id, scheduled_for=scheduled_for, now=NOW
        )
        delays: list[float] = []

        async def controlled_sleep(delay: float) -> None:
            delays.append(delay)

        result = asyncio.run(
            coordinator.wait_and_dispatch(
                scheduled_for=scheduled_for,
                now=NOW,
                owner="owner",
                sleep=controlled_sleep,
                clock=lambda: scheduled_for,
            )
        )

        self.assertEqual(delays, [30.0])
        self.assertEqual(tuple(entry.state for entry in result), (WorkState.COMPLETED,))
        self.assertEqual(SimulationReportRow.objects.count(), 1)

    def test_delayed_async_wake_marks_expired_work_without_dispatch(self) -> None:
        request = _bonus_request()
        coordinator = self._coordinator(
            RoutingConfiguration(bonus=EngineModes(simulation=True)), request.opportunity_id
        )
        scheduled_for = NOW + timedelta(seconds=30)
        expires_at = NOW + timedelta(seconds=45)
        coordinator.schedule(
            request,
            owner="owner",
            correlation_id=CORRELATION_ID,
            scheduled_for=scheduled_for,
            expires_at=expires_at,
        )

        async def delayed_sleep(_: float) -> None:
            return None

        (result,) = asyncio.run(
            coordinator.wait_and_dispatch(
                scheduled_for=scheduled_for,
                now=NOW,
                owner="owner",
                sleep=delayed_sleep,
                clock=lambda: expires_at + timedelta(seconds=1),
            )
        )

        self.assertEqual(result.state, WorkState.EXPIRED)
        self.assertEqual(SimulationReportRow.objects.count(), 0)

    def test_recheck_cannot_be_rescheduled_past_its_expiry(self) -> None:
        request = _sports_request()
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(sports_capital=EngineModes(execution=True)),
            queue_repository=ModeWorkQueueRepository(),
            mode_request_handlers=_handlers(request.opportunity_id, RevalidationOutcome.CHANGED),
        )
        (item,) = coordinator.schedule(
            request,
            owner="owner",
            correlation_id=CORRELATION_ID,
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=1),
        )
        coordinator.dispatch_due(now=NOW, owner="owner")

        with self.assertRaisesMessage(ValueError, "rescheduled work must remain before expires_at"):
            ModeWorkQueueRepository().reschedule(
                item.work.id, scheduled_for=NOW + timedelta(minutes=1), now=NOW
            )

    def test_non_successful_result_statuses_persist_safe_queue_decisions(self) -> None:
        expected = {
            ResultStatus.NOT_YET_AVAILABLE: WorkState.RECHECK,
            ResultStatus.PARTIAL: WorkState.RECHECK,
            ResultStatus.FAILED: WorkState.FAILED,
            ResultStatus.UNKNOWN: WorkState.FAILED,
        }
        results: dict[ResultStatus, WorkState] = {}
        queue_items = {}
        for index, (status, state) in enumerate(expected.items(), start=1):
            request = _sports_request().model_copy(
                update={"opportunity_id": f"sports-result-{status.value}"}
            )
            coordinator = ModeDispatchCoordinator(
                RoutingConfiguration(sports_capital=EngineModes(execution=True)),
                queue_repository=ModeWorkQueueRepository(),
                mode_request_handlers=_handlers(request.opportunity_id, result_status=status),
            )
            coordinator.schedule(
                request,
                owner="owner",
                correlation_id=UUID(f"12345678-1234-5678-1234-{index:012d}"),
                scheduled_for=NOW,
                expires_at=NOW + timedelta(minutes=5),
            )
            (result,) = coordinator.dispatch_due(now=NOW, owner="owner")
            results[status] = result.state
            queue_items[status] = result

        self.assertEqual(results, expected)
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)

        partial_item = queue_items[ResultStatus.PARTIAL]
        self.assertEqual(partial_item.state, WorkState.RECHECK)
        self.assertEqual(partial_item.history[-1].reason, "result_partial")

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
