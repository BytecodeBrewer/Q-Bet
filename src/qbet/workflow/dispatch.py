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
from qbet.monitoring import MonitoringLevel, MonitoringRecord
from qbet.request_handler import ModeRequestHandlers
from qbet.request_handler.models import ResultStatus
from qbet.simulation.models import SimulationEngine, SimulationRunConfig
from qbet.simulation.workflow import WorkflowSimulationRequest, WorkflowSimulationRunner
from qbet.storage.ledger import ExecutionRecordRepository, PortfolioLedgerRepository, ModeWorkQueueRepository
from qbet.storage.monitoring import PostgresMonitoringRepository
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
        monitoring_writer: PostgresMonitoringRepository | None = None,
    ) -> None:
        self._configuration = configuration
        self._queue_repository = queue_repository or ModeWorkQueueRepository()
        self._mode_request_handlers = mode_request_handlers
        self._monitoring_writer = monitoring_writer or PostgresMonitoringRepository()

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
                    self._save_queue(processing.transition(WorkState.EXPIRED, now=now))
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
            self._record_workflow(processing, workflow_result)
            if workflow_result.request_handler_result is not None:
                self._record_event(
                    processing, stage="request_handler", event_type="result",
                    status=workflow_result.request_handler_result.status.value,
                    reason_code=workflow_result.request_handler_result.reason_code,
                )
            if workflow_result.final_decision is WorkflowDecision.RECHECK:
                processed.append(
                    self._save_queue(
                        processing.transition(WorkState.RECHECK, now=now, reason="revalidation_recheck")
                    )
                )
                continue
            if workflow_result.final_decision is WorkflowDecision.REJECT:
                processed.append(
                    self._save_queue(
                        processing.transition(WorkState.CANCELLED, now=now, reason="revalidation_rejected")
                    )
                )
                continue
            handler_result = workflow_result.request_handler_result
            if handler_result is not None and handler_result.status is ResultStatus.NOT_YET_AVAILABLE:
                processed.append(
                    self._save_queue(
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
                    self._save_queue(
                        processing.transition(
                            WorkState.RECHECK, now=now, reason="result_partial"
                        )
                    )
                )
                continue
            if handler_result is not None and handler_result.status is ResultStatus.CANCELLED:
                processed.append(
                    self._save_queue(
                        processing.transition(WorkState.CANCELLED, now=now, reason="result_cancelled")
                    )
                )
                continue
            if handler_result is not None and handler_result.status is not ResultStatus.SUCCESS:
                processed.append(
                    self._save_queue(
                        processing.transition(WorkState.FAILED, now=now, reason="result_unavailable")
                    )
                )
                continue
            if processing.work.mode is WorkflowMode.SIMULATION:
                self._run_simulation(processing)
            else:
                self._run_execution(processing, now=now, owner=owner)
            processed.append(self._save_queue(processing.transition(WorkState.COMPLETED, now=now)))
        return tuple(processed)

    def _record_workflow(self, item: QueuedWorkItem, workflow_result) -> None:
        """Persist only safe transition metadata for the administrator read model."""

        for transition in workflow_result.transitions:
            level = (
                MonitoringLevel.ERROR
                if transition.decision is WorkflowDecision.REJECT
                else MonitoringLevel.WARNING
                if transition.decision is WorkflowDecision.RECHECK
                else MonitoringLevel.INFO
            )
            self._monitoring_writer.append(
                MonitoringRecord(
                    correlation_id=workflow_result.correlation_id,
                    occurred_at=datetime.now(UTC),
                    engine=item.work.engine,
                    mode=item.work.mode.value,
                    stage=transition.stage.value,
                    event_type=transition.kind.value,
                    status=transition.decision.value,
                    reason_code=transition.reason,
                    level=level,
                    references={
                        "work_id": str(item.work.id),
                        "opportunity_id": item.work.opportunity_id,
                    },
                )
            )

    def _save_queue(self, item: QueuedWorkItem) -> QueuedWorkItem:
        saved = self._queue_repository.save(item)
        event = saved.history[-1]
        self._record_event(
            saved, stage="queue", event_type="state_transition", status=saved.state.value,
            reason_code=event.reason,
        )
        return saved

    def _record_event(
        self,
        item: QueuedWorkItem,
        *,
        stage: str,
        event_type: str,
        status: str,
        reason_code: str | None = None,
        duration_ms: int | None = None,
        references: dict[str, str] | None = None,
    ) -> None:
        level = (
            MonitoringLevel.ERROR if status in {"failed", "cancelled", "rejected"}
            else MonitoringLevel.WARNING if status in {"recheck", "partial"}
            else MonitoringLevel.INFO
        )
        self._monitoring_writer.append(MonitoringRecord(
            correlation_id=item.work.correlation_id, occurred_at=datetime.now(UTC),
            engine=item.work.engine, mode=item.work.mode.value, stage=stage,
            event_type=event_type, status=status, reason_code=reason_code, level=level,
            duration_ms=duration_ms,
            references={"work_id": str(item.work.id), "opportunity_id": item.work.opportunity_id,
                        **(references or {})},
        ))

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
        runner = WorkflowSimulationRunner(
            report_store=PostgresSimulationReportStore(),
            mode_request_handlers=self._mode_request_handlers,
            ledger_writer=ledger_repository.save,
        )
        result = runner.run(
            WorkflowSimulationRequest(
                config=SimulationRunConfig(engine=engine, starting_capital=Decimal("100")),
                opportunities=(item.request,),
                provider_state=ProviderState(provider_id="sandbox", active_bets_count=0),
                correlation_id=item.work.correlation_id,
            )
        )
        self._record_event(
            item, stage="simulation", event_type="lifecycle", status=result.simulation_result.status.value,
            references={"report_id": str(runner.last_report.run_id)} if runner.last_report else {},
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
        record, ledger = ExecutionService(
            ledger_writer=ledger_repository,
            execution_writer=ExecutionRecordRepository(),
        ).decide(ExecutionRecord(proposal=proposal), ledger, actor=owner, owner=owner, approve=True, now=now)
        self._record_event(
            item, stage="execution", event_type="lifecycle", status=record.state.value,
            reason_code=record.error,
            references={"execution_id": str(item.work.id), "ledger_mode": ledger.balance.mode},
        )
