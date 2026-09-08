"""Controlled fan-out, scheduling, revalidation, and sandbox dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import transaction

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
from qbet.storage.ledger import (
    AuthoritativePersistenceError,
    AuthoritativeStateConflict,
    ExecutionStateRepository,
    ModeWorkQueueRepository,
    PortfolioLedgerRepository,
)
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.storage.postgres import PostgresSimulationReportStore
from qbet.workflow.models import (
    WorkflowDecision,
    WorkflowMode,
    WorkflowRequest,
    WorkflowStage,
)
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
        engine: V1Engine = (
            "bonus" if isinstance(request, BonusEngineRequest) else "sports_capital"
        )
        routes = resolve_routes(
            self._configuration,
            engine,
            request.opportunity_id,
            correlation_id,
            owner,
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
                    processing,
                    stage="request_handler",
                    event_type="result",
                    status=workflow_result.request_handler_result.status.value,
                    reason_code=workflow_result.request_handler_result.reason_code,
                    occurred_at=now,
                )

            if workflow_result.final_decision is WorkflowDecision.RECHECK:
                processed.append(
                    self._save_queue(
                        processing.transition(
                            WorkState.RECHECK,
                            now=now,
                            reason="revalidation_recheck",
                        )
                    )
                )
                continue
            if workflow_result.final_decision is WorkflowDecision.REJECT:
                processed.append(
                    self._save_queue(
                        processing.transition(
                            WorkState.CANCELLED,
                            now=now,
                            reason="revalidation_rejected",
                        )
                    )
                )
                continue

            handler_result = workflow_result.request_handler_result
            if (
                handler_result is not None
                and handler_result.status is ResultStatus.NOT_YET_AVAILABLE
            ):
                processed.append(
                    self._save_queue(
                        processing.transition(
                            WorkState.RECHECK,
                            now=now,
                            reason="result_not_yet_available",
                        )
                    )
                )
                continue
            if handler_result is not None and handler_result.status is ResultStatus.PARTIAL:
                processed.append(
                    self._save_queue(
                        processing.transition(
                            WorkState.RECHECK,
                            now=now,
                            reason="result_partial",
                        )
                    )
                )
                continue
            if (
                handler_result is not None
                and handler_result.status is ResultStatus.CANCELLED
            ):
                processed.append(
                    self._save_queue(
                        processing.transition(
                            WorkState.CANCELLED,
                            now=now,
                            reason="result_cancelled",
                        )
                    )
                )
                continue
            if handler_result is not None and handler_result.status is not ResultStatus.SUCCESS:
                processed.append(
                    self._save_queue(
                        processing.transition(
                            WorkState.FAILED,
                            now=now,
                            reason="result_unavailable",
                        )
                    )
                )
                continue

            try:
                processed.append(
                    self._complete_mode_atomically(
                        processing,
                        now=now,
                        owner=owner,
                    )
                )
            except Exception as error:
                processed.append(
                    self._fail_mode_atomically(
                        processing,
                        now=now,
                        reason_code=self._safe_failure_reason(error),
                    )
                )
        return tuple(processed)

    def _complete_mode_atomically(
        self,
        item: QueuedWorkItem,
        *,
        now: datetime,
        owner: str,
    ) -> QueuedWorkItem:
        """Commit mode state, Monitoring projection, and terminal queue state together."""

        with transaction.atomic():
            if item.work.mode is WorkflowMode.SIMULATION:
                self._run_simulation(item, now=now)
            else:
                self._run_execution(item, now=now, owner=owner)
            return self._save_queue(item.transition(WorkState.COMPLETED, now=now))

    def _fail_mode_atomically(
        self,
        item: QueuedWorkItem,
        *,
        now: datetime,
        reason_code: str,
    ) -> QueuedWorkItem:
        """Persist a safe technical failure and its queue terminal state together."""

        with transaction.atomic():
            failed = self._queue_repository.save(
                item.transition(WorkState.FAILED, now=now, reason=reason_code)
            )
            self._record_event(
                failed,
                stage=failed.work.mode.value,
                event_type="lifecycle_error",
                status="failed",
                reason_code=reason_code,
                occurred_at=now,
            )
            self._record_queue_event(failed)
            return failed

    @staticmethod
    def _safe_failure_reason(error: Exception) -> str:
        if isinstance(error, AuthoritativePersistenceError):
            return "authoritative_execution_state_unavailable"
        if isinstance(error, AuthoritativeStateConflict):
            return "authoritative_execution_state_conflict"
        return "mode_execution_failed"

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
        """Persist a queue transition and its Monitoring event in one transaction."""

        with transaction.atomic():
            saved = self._queue_repository.save(item)
            self._record_queue_event(saved)
            return saved

    def _record_queue_event(self, item: QueuedWorkItem) -> None:
        event = item.history[-1]
        self._record_event(
            item,
            stage="queue",
            event_type="state_transition",
            status=item.state.value,
            reason_code=event.reason,
            occurred_at=event.recorded_at,
        )

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
        occurred_at: datetime | None = None,
    ) -> None:
        level = (
            MonitoringLevel.ERROR
            if status in {"failed", "cancelled", "rejected"}
            else MonitoringLevel.WARNING
            if status in {"recheck", "partial"}
            else MonitoringLevel.INFO
        )
        self._monitoring_writer.append(
            MonitoringRecord(
                correlation_id=item.work.correlation_id,
                occurred_at=occurred_at or datetime.now(UTC),
                engine=item.work.engine,
                mode=item.work.mode.value,
                stage=stage,
                event_type=event_type,
                status=status,
                reason_code=reason_code,
                level=level,
                duration_ms=duration_ms,
                references={
                    "work_id": str(item.work.id),
                    "opportunity_id": item.work.opportunity_id,
                    **(references or {}),
                },
            )
        )

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
            now=actual_now,
            owner=owner,
        )

    def _run_simulation(self, item: QueuedWorkItem, *, now: datetime) -> None:
        engine = SimulationEngine(item.work.engine)
        ledger_repository = PortfolioLedgerRepository()
        initial_ledger = PortfolioLedger(
            balance=PortfolioBalance(
                mode="simulation",
                currency=item.request.currency,
                available=Decimal("100"),
            )
        )
        runner = WorkflowSimulationRunner(
            report_store=PostgresSimulationReportStore(),
            mode_request_handlers=self._mode_request_handlers,
            simulation_ledger=initial_ledger,
            ledger_writer=ledger_repository.save,
        )
        result = runner.run(
            WorkflowSimulationRequest(
                config=SimulationRunConfig(
                    engine=engine,
                    starting_capital=Decimal("100"),
                ),
                opportunities=(item.request,),
                provider_state=ProviderState(
                    provider_id="sandbox",
                    active_bets_count=0,
                ),
                correlation_id=item.work.correlation_id,
            )
        )

        for workflow_result in result.workflow_results:
            self._record_workflow(item, workflow_result)

        self._record_event(
            item,
            stage="simulation",
            event_type="lifecycle",
            status=result.simulation_result.status.value,
            occurred_at=now,
            references=(
                {"report_id": str(runner.last_report.run_id)}
                if runner.last_report
                else {}
            ),
        )
        if runner.last_ledger is not None:
            self._record_ledger_transitions(
                item,
                initial_ledger,
                runner.last_ledger,
                dispatch_prefix=str(result.correlation_id),
                occurred_at=now,
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
        state_repository = ExecutionStateRepository()
        initial_ledger = PortfolioLedger(
            balance=PortfolioBalance(
                mode="execution",
                currency=item.request.currency,
                available=Decimal("1000"),
            )
        )
        record, ledger = state_repository.load_or_create(
            ExecutionRecord(proposal=proposal),
            initial_ledger,
        )
        updated_record, updated_ledger = ExecutionService().decide(
            record,
            ledger,
            actor=owner,
            owner=owner,
            approve=True,
            now=now,
        )
        persisted_record, persisted_ledger = state_repository.persist(
            updated_record,
            updated_ledger,
        )

        references = {
            "execution_id": str(item.work.id),
            "ledger_mode": persisted_ledger.balance.mode,
        }
        for state in persisted_record.transitions:
            self._record_event(
                item,
                stage="execution",
                event_type="lifecycle_transition",
                status=state.value,
                reason_code=(
                    persisted_record.error if state is persisted_record.state else None
                ),
                references=references,
                occurred_at=now,
            )

        self._record_ledger_transitions(
            item,
            ledger,
            persisted_ledger,
            dispatch_id=str(item.work.id),
            occurred_at=now,
        )
        if persisted_record.result is not None:
            self._record_event(
                item,
                stage="settlement",
                event_type="result",
                status=persisted_record.result.status,
                reason_code=persisted_record.error,
                references={
                    **references,
                    "settlement_id": str(persisted_record.result.dispatch_id),
                },
                occurred_at=persisted_record.result.observed_at,
            )

    def _record_ledger_transitions(
        self,
        item: QueuedWorkItem,
        before: PortfolioLedger,
        after: PortfolioLedger,
        *,
        dispatch_id: str | None = None,
        dispatch_prefix: str | None = None,
        occurred_at: datetime,
    ) -> None:
        """Project newly applied ledger commands with reconstructable capital state."""

        cursor = before
        for command_id, command in after.commands.items():
            if command_id in before.commands:
                continue
            if dispatch_id is not None and command.dispatch_id != dispatch_id:
                continue
            if (
                dispatch_prefix is not None
                and not command.dispatch_id.startswith(dispatch_prefix)
            ):
                continue

            updated, decision = cursor.apply(command)
            if not decision.accepted:
                raise ValueError("monitoring_ledger_replay_failed")
            cursor = updated
            position = cursor.positions.get(command.dispatch_id)
            balance = cursor.balance
            status = {
                "reserve": "reserved",
                "release": "released",
                "lock": "locked",
                "pending": "pending",
                "settle": "settled",
                "fail": "failed",
                "cost": "cost",
            }.get(command.operation.value, command.operation.value)

            self._record_event(
                item,
                stage=(
                    "settlement"
                    if command.operation.value in {"settle", "fail"}
                    else "ledger"
                ),
                event_type="capital_transition",
                status=status,
                reason_code=command.operation.value,
                occurred_at=occurred_at,
                references={
                    "ledger_command_id": command.id,
                    "dispatch_id": command.dispatch_id,
                    "capital_amount": str(command.amount),
                    "ledger_position_state": (
                        position.state if position is not None else status
                    ),
                    "available": str(balance.available),
                    "reserved": str(balance.reserved),
                    "locked": str(balance.locked),
                    "pending": str(balance.pending),
                    "settled": str(balance.settled),
                    "cost": str(balance.cost),
                },
            )
