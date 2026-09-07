"""Controlled fan-out, scheduling, revalidation, and sandbox dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from asgiref.sync import sync_to_async
from qbet.domain.ledger import PortfolioBalance
from qbet.domain.verification import ProviderState
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.execution.models import ExecutionProposal, ExecutionRecord
from qbet.execution.service import ExecutionService
from qbet.execution.sandbox import valuation
from qbet.ledger import PortfolioLedger
from qbet.request_handler import ModeRequestHandlers
from qbet.request_handler.models import ResultStatus
from qbet.simulation.models import SimulationEngine, SimulationRunConfig
from qbet.simulation.workflow import WorkflowSimulationRequest, WorkflowSimulationRunner
from qbet.storage.ledger import ExecutionRecordRepository, PortfolioLedgerRepository, ModeWorkQueueRepository
from qbet.storage.postgres import PostgresSimulationReportStore
from qbet.workflow.models import WorkflowDecision, WorkflowMode, WorkflowRequest, WorkflowStage
from qbet.workflow.orchestrator import WorkflowOrchestrator
from qbet.workflow.queue import QueuedWorkItem, WorkState
from qbet.workflow.routing import RoutingConfiguration, V1Engine, resolve_routes


class ModeDispatchCoordinator:
    """Owns the durable hand-off from one opportunity to isolated mode work items."""

    def __init__(
        self,
        configuration: RoutingConfiguration,
        *,
        queue_repository: ModeWorkQueueRepository | None = None,
        mode_request_handlers: ModeRequestHandlers | None = None,
    ) -> None:
        self._configuration = configuration
        self._queue_repository = queue_repository or ModeWorkQueueRepository()
        self._mode_request_handlers = mode_request_handlers

    def schedule(
        self,
        request: BonusEngineRequest | SportsCapitalEngineRequest,
        *,
        owner: str,
        correlation_id: UUID,
        scheduled_for: datetime,
        expires_at: datetime,
    ) -> tuple[QueuedWorkItem, ...]:
        engine: V1Engine = "bonus" if isinstance(request, BonusEngineRequest) else "sports_capital"
        routes = resolve_routes(
            self._configuration, engine, request.opportunity_id, correlation_id, owner
        )
        return tuple(
            self._queue_repository.enqueue(
                QueuedWorkItem.pending(
                    route,
                    request,
                    scheduled_for=scheduled_for,
                    expires_at=expires_at,
                )
            )
            for route in routes
        )

    def dispatch_due(self, *, now: datetime, owner: str) -> tuple[QueuedWorkItem, ...]:
        processed: list[QueuedWorkItem] = []
        for processing in self._queue_repository.claim_due(now):
            if now >= processing.expires_at:
                processed.append(
                    self._queue_repository.save(processing.transition(WorkState.EXPIRED, now=now))
                )
                continue
            workflow_result = WorkflowOrchestrator(
                mode_request_handlers=self._mode_request_handlers
            ).process(
                WorkflowRequest(
                    id=str(processing.work.id),
                    opportunity_id=processing.work.opportunity_id,
                    mode=processing.work.mode,
                    correlation_id=processing.work.correlation_id,
                    stages=(
                        WorkflowStage.DATA_AGGREGATION,
                        WorkflowStage.ENGINE_PREPARATION,
                        WorkflowStage.CALCULATION,
                        WorkflowStage.DOMAIN_RISK,
                        WorkflowStage.LIQUIDITY_CHECK,
                        WorkflowStage.DISPATCH,
                    ),
                )
            )
            if workflow_result.final_decision is WorkflowDecision.RECHECK:
                processed.append(
                    self._queue_repository.save(
                        processing.transition(WorkState.RECHECK, now=now, reason="revalidation_recheck")
                    )
                )
                continue
            if workflow_result.final_decision is WorkflowDecision.REJECT:
                processed.append(
                    self._queue_repository.save(
                        processing.transition(WorkState.CANCELLED, now=now, reason="revalidation_rejected")
                    )
                )
                continue
            handler_result = workflow_result.request_handler_result
            if handler_result is not None and handler_result.status is ResultStatus.NOT_YET_AVAILABLE:
                processed.append(
                    self._queue_repository.save(
                        processing.transition(
                            WorkState.RECHECK, now=now, reason="result_not_yet_available"
                        )
                    )
                )
                continue
            if handler_result is not None and handler_result.status is ResultStatus.PARTIAL:
                # A request-handler partial result has no validated settlement amount. Keep the
                # durable work item pending for a complete result rather than treating it as a loss.
                processed.append(
                    self._queue_repository.save(
                        processing.transition(
                            WorkState.RECHECK, now=now, reason="result_partial"
                        )
                    )
                )
                continue
            if handler_result is not None and handler_result.status is ResultStatus.CANCELLED:
                processed.append(
                    self._queue_repository.save(
                        processing.transition(WorkState.CANCELLED, now=now, reason="result_cancelled")
                    )
                )
                continue
            if handler_result is not None and handler_result.status is not ResultStatus.SUCCESS:
                processed.append(
                    self._queue_repository.save(
                        processing.transition(WorkState.FAILED, now=now, reason="result_unavailable")
                    )
                )
                continue
            if processing.work.mode is WorkflowMode.SIMULATION:
                self._run_simulation(processing)
            else:
                self._run_execution(processing, now=now, owner=owner)
            processed.append(self._queue_repository.save(processing.transition(WorkState.COMPLETED, now=now)))
        return tuple(processed)

    async def wait_and_dispatch(
        self,
        *,
        scheduled_for: datetime,
        now: datetime,
        owner: str,
        sleep=asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> tuple[QueuedWorkItem, ...]:
        """Await a controlled clock boundary before atomically claiming due work."""

        delay = max(0.0, (scheduled_for - now).total_seconds())
        await sleep(delay)
        actual_now = clock()
        return await sync_to_async(self.dispatch_due, thread_sensitive=True)(
            now=actual_now, owner=owner
        )

    def _run_simulation(self, item: QueuedWorkItem) -> None:
        engine = SimulationEngine(item.work.engine)
        ledger_repository = PortfolioLedgerRepository()
        WorkflowSimulationRunner(
            report_store=PostgresSimulationReportStore(),
            mode_request_handlers=self._mode_request_handlers,
            ledger_writer=ledger_repository.save,
        ).run(
            WorkflowSimulationRequest(
                config=SimulationRunConfig(engine=engine, starting_capital=Decimal("100")),
                opportunities=(item.request,),
                provider_state=ProviderState(provider_id="sandbox", active_bets_count=0),
                correlation_id=item.work.correlation_id,
            )
        )

    def _run_execution(self, item: QueuedWorkItem, *, now: datetime, owner: str) -> None:
        capital, payout = valuation(item.request)
        proposal = ExecutionProposal(
            work=item.work,
            request=item.request,
            expires_at=item.expires_at,
            currency=item.request.currency,
            capital_required=capital,
            payout=payout,
        )
        ledger_repository = PortfolioLedgerRepository()
        ledger = ledger_repository.load(mode="execution", currency=item.request.currency) or PortfolioLedger(
            balance=PortfolioBalance(mode="execution", currency=item.request.currency, available=Decimal("1000"))
        )
        ExecutionService(
            ledger_writer=ledger_repository,
            execution_writer=ExecutionRecordRepository(),
        ).decide(ExecutionRecord(proposal=proposal), ledger, actor=owner, owner=owner, approve=True, now=now)
