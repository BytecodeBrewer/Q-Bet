"""Controlled fan-out, scheduling, revalidation, and sandbox dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import DatabaseError, transaction

from qbet.domain.ledger import PortfolioBalance
from qbet.domain.verification import ProviderState
from qbet.engines import BonusEngineRequest, SportsCapitalEngineRequest
from qbet.execution.models import ExecutionProposal, ExecutionRecord, Lifecycle
from qbet.execution.service import ExecutionService
from qbet.execution.sandbox import valuation
from qbet.ledger import PortfolioLedger
from qbet.monitoring import MonitoringLevel, MonitoringRecord
from qbet.notifications import (
    ActiveUserNotificationRecipientResolver,
    DjangoEmailTransport,
    ExecutionNotificationService,
    NotificationRecipientResolver,
)
from qbet.request_handler import ModeRequestHandlers
from qbet.request_handler.models import ResultStatus
from qbet.simulation.models import SimulationEngine, SimulationRunConfig
from qbet.simulation.workflow import WorkflowSimulationRequest, WorkflowSimulationRunner
from qbet.storage.ledger import (
    AuthoritativePersistenceError,
    AuthoritativeStateConflict,
    ExecutionStateRepository,
    ModeWorkQueueRepository,
    RoutingConfigurationRepository,
)
from qbet.storage.monitoring import MonitoringPersistenceError, PostgresMonitoringRepository
from qbet.storage.notifications import (
    NotificationPersistenceError,
    PostgresNotificationRepository,
)
from qbet.storage.postgres import PostgresSimulationReportStore
from qbet.storage.simulation_ledger import SimulationPortfolioLedgerRepository
from qbet.workflow.models import (
    WorkflowDecision,
    WorkflowMode,
    WorkflowRequest,
    WorkflowStage,
)
from qbet.workflow.orchestrator import WorkflowOrchestrator
from qbet.workflow.queue import QueuedWorkItem, WorkState
from qbet.workflow.readiness import PipelineReadinessProvider, Phase2PipelineReadiness
from qbet.workflow.routing import RoutingConfiguration, V1Engine


class ModeDispatchCoordinator:
    """Owns the durable hand-off from one opportunity to isolated mode work items."""

    def __init__(
        self,
        configuration: RoutingConfiguration | None = None,
        *,
        routing_configuration_loader: Callable[[], RoutingConfiguration | None] | None = None,
        queue_repository: ModeWorkQueueRepository | None = None,
        mode_request_handlers: ModeRequestHandlers | None = None,
        monitoring_writer: PostgresMonitoringRepository | None = None,
        readiness_provider: PipelineReadinessProvider | None = None,
        notification_service: ExecutionNotificationService | None = None,
        notification_recipient_resolver: NotificationRecipientResolver | None = None,
    ) -> None:
        if configuration is not None and routing_configuration_loader is not None:
            raise ValueError(
                "routing configuration and routing configuration loader are mutually exclusive"
            )
        if configuration is None and routing_configuration_loader is None:
            routing_configuration_loader = RoutingConfigurationRepository().load
        self._routing_orchestrator = WorkflowOrchestrator(
            routing_configuration=configuration,
            routing_configuration_loader=routing_configuration_loader,
        )
        self._queue_repository = queue_repository or ModeWorkQueueRepository()
        self._mode_request_handlers = mode_request_handlers
        self._monitoring_writer = monitoring_writer or PostgresMonitoringRepository()
        self._readiness_provider = readiness_provider or Phase2PipelineReadiness()
        self._notification_service = notification_service or ExecutionNotificationService(
            repository=PostgresNotificationRepository(),
            transport=DjangoEmailTransport(),
            monitoring_writer=self._monitoring_writer,
        )
        self._notification_recipient_resolver = (
            notification_recipient_resolver or ActiveUserNotificationRecipientResolver()
        )

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
        routes = self._routing_orchestrator.route(
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
            persisted_owner = processing.work.owner or owner
            try:
                # Execution checkpoints must commit before adapter side effects. Do not
                # wrap an entire claimed item in one outer transaction.
                outcome = self._dispatch_claimed(
                    processing,
                    now=now,
                    owner=persisted_owner,
                )
            except Exception as error:
                outcome = self._fail_after_error(processing, now=now, error=error)
            processed.append(outcome)
        return tuple(processed)

    def _dispatch_claimed(
        self,
        processing: QueuedWorkItem,
        *,
        now: datetime,
        owner: str,
    ) -> QueuedWorkItem:
        if now >= processing.expires_at:
            self._cancel_approved_execution(
                processing,
                now=now,
                reason="execution_expired_before_dispatch",
            )
            return self._save_queue(processing.transition(WorkState.EXPIRED, now=now))

        readiness = self._readiness_provider.snapshot(
            processing.work.engine,
            processing.work.mode,
        )
        blocked = None
        for stage in readiness:
            self._record_event(
                processing,
                stage=stage.stage.value,
                event_type="readiness",
                status="ready" if stage.ready else "not_ready",
                reason_code=stage.reason,
                occurred_at=now,
            )
            if blocked is None and not stage.ready:
                blocked = stage
        if blocked is not None:
            return self._save_queue(
                processing.transition(
                    WorkState.RECHECK,
                    now=now,
                    reason=f"pipeline_not_ready:{blocked.stage.value}",
                )
            )

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
        self._record_workflow(processing, workflow_result, occurred_at=now)
        if workflow_result.request_handler_result is not None:
            self._record_event(
                processing,
                stage="request_handler",
                event_type="result",
                status=workflow_result.request_handler_result.status.value,
                reason_code=workflow_result.request_handler_result.reason_code,
                occurred_at=now,
                references={"subprocess_id": str(workflow_result.request_id)},
            )

        if workflow_result.final_decision is WorkflowDecision.RECHECK:
            if self._cancel_approved_execution(
                processing,
                now=now,
                reason="approved_execution_revalidation_recheck",
            ):
                return self._save_queue(
                    processing.transition(
                        WorkState.CANCELLED,
                        now=now,
                        reason="approved_execution_revalidation_recheck",
                    )
                )
            return self._save_queue(
                processing.transition(
                    WorkState.RECHECK,
                    now=now,
                    reason="revalidation_recheck",
                )
            )
        if workflow_result.final_decision is WorkflowDecision.REJECT:
            self._cancel_approved_execution(
                processing,
                now=now,
                reason="revalidation_rejected",
            )
            return self._save_queue(
                processing.transition(
                    WorkState.CANCELLED,
                    now=now,
                    reason="revalidation_rejected",
                )
            )

        handler_result = workflow_result.request_handler_result
        if handler_result is not None and handler_result.status is ResultStatus.NOT_YET_AVAILABLE:
            return self._save_queue(
                processing.transition(
                    WorkState.RECHECK,
                    now=now,
                    reason="result_not_yet_available",
                )
            )
        if handler_result is not None and handler_result.status is ResultStatus.PARTIAL:
            return self._save_queue(
                processing.transition(
                    WorkState.RECHECK,
                    now=now,
                    reason="result_partial",
                )
            )
        if handler_result is not None and handler_result.status is ResultStatus.CANCELLED:
            self._cancel_approved_execution(
                processing,
                now=now,
                reason="result_cancelled",
            )
            return self._save_queue(
                processing.transition(
                    WorkState.CANCELLED,
                    now=now,
                    reason="result_cancelled",
                )
            )
        if handler_result is not None and handler_result.status is not ResultStatus.SUCCESS:
            self._cancel_approved_execution(
                processing,
                now=now,
                reason="result_unavailable",
            )
            return self._save_queue(
                processing.transition(
                    WorkState.FAILED,
                    now=now,
                    reason="result_unavailable",
                )
            )

        return self._complete_mode_atomically(
            processing,
            now=now,
            owner=owner,
        )

    def _complete_mode_atomically(
        self,
        item: QueuedWorkItem,
        *,
        now: datetime,
        owner: str,
    ) -> QueuedWorkItem:
        """Finish one mode without spanning execution side effects with a DB transaction."""

        if item.work.mode is WorkflowMode.EXECUTION:
            state_repository, record, ledger = self._execution_state(item)
            expected_owner = record.proposal.work.owner
            if expected_owner is not None and owner != expected_owner:
                raise PermissionError("proposal_owner_required")

            if record.state is Lifecycle.AWAITING_APPROVAL:
                self._record_event(
                    item,
                    stage="execution",
                    event_type="approval_required",
                    status=Lifecycle.AWAITING_APPROVAL.value,
                    reason_code="explicit_user_approval_required",
                    occurred_at=now,
                    references={"execution_id": str(item.work.id)},
                )
                return self._save_queue(
                    item.transition(
                        WorkState.RECHECK,
                        now=now,
                        reason="execution_approval_required",
                    )
                )

            if record.state in {Lifecycle.REJECTED, Lifecycle.CANCELLED}:
                return self._save_queue(
                    item.transition(
                        WorkState.CANCELLED,
                        now=now,
                        reason="execution_not_approved",
                    )
                )
            if record.state is Lifecycle.FAILED:
                return self._save_queue(
                    item.transition(
                        WorkState.FAILED,
                        now=now,
                        reason=record.error or "execution_failed",
                    )
                )
            if record.state is Lifecycle.SETTLED:
                return self._save_queue(item.transition(WorkState.COMPLETED, now=now))

            self._notify_after_revalidation(record, item, now=now)

            persisted_record = self._run_execution(
                item,
                state_repository=state_repository,
                record=record,
                ledger=ledger,
                now=now,
            )
            if persisted_record.state is Lifecycle.SETTLED:
                return self._save_queue(item.transition(WorkState.COMPLETED, now=now))
            if persisted_record.state in {Lifecycle.REJECTED, Lifecycle.CANCELLED}:
                return self._save_queue(
                    item.transition(
                        WorkState.CANCELLED,
                        now=now,
                        reason=persisted_record.error or "execution_rejected",
                    )
                )
            return self._save_queue(
                item.transition(
                    WorkState.FAILED,
                    now=now,
                    reason=persisted_record.error or "execution_failed",
                )
            )

        with transaction.atomic():
            self._run_simulation(item, now=now)
            return self._save_queue(item.transition(WorkState.COMPLETED, now=now))

    def _notify_after_revalidation(
        self,
        record: ExecutionRecord,
        item: QueuedWorkItem,
        *,
        now: datetime,
    ) -> None:
        approval = record.approval
        if record.state is not Lifecycle.APPROVED or approval is None:
            return
        try:
            recipients = self._notification_recipient_resolver.resolve()
        except (DatabaseError, NotificationPersistenceError, ValueError):
            self._record_event(
                item,
                stage="notification",
                event_type="notification_unavailable",
                status="failed",
                reason_code="notification_persistence_unavailable",
                occurred_at=now,
            )
            return

        if not recipients:
            self._record_event(
                item,
                stage="notification",
                event_type="notification_delivery",
                status="failed",
                reason_code="notification_no_active_recipients",
                occurred_at=now,
            )
            return

        for recipient in recipients:
            try:
                outcome = self._notification_service.notify(
                    record,
                    item,
                    recipient,
                    now=now,
                )
            except (DatabaseError, NotificationPersistenceError, ValueError):
                self._record_event(
                    item,
                    stage="notification",
                    event_type="notification_unavailable",
                    status="failed",
                    reason_code="notification_persistence_unavailable",
                    occurred_at=now,
                    references={"recipient_id": recipient.user_id},
                )
                continue
            self._record_event(
                item,
                stage="notification",
                event_type="notification_delivery",
                status=(outcome.task.status.value if outcome.task is not None else "failed"),
                reason_code=outcome.reason_code,
                occurred_at=now,
                references={"recipient_id": recipient.user_id},
            )

    def _fail_after_error(
        self,
        item: QueuedWorkItem,
        *,
        now: datetime,
        error: Exception,
    ) -> QueuedWorkItem:
        """Fail a claimed item even when the Monitoring writer itself is unavailable."""

        reason_code = self._safe_failure_reason(error)
        try:
            return self._fail_mode_atomically(
                item,
                now=now,
                reason_code=reason_code,
            )
        except Exception:
            return self._queue_repository.save(
                item.transition(
                    WorkState.FAILED,
                    now=now,
                    reason=reason_code,
                )
            )

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
        if isinstance(error, MonitoringPersistenceError):
            return "monitoring_persistence_unavailable"
        return "mode_execution_failed"

    def _cancel_approved_execution(
        self,
        item: QueuedWorkItem,
        *,
        now: datetime,
        reason: str,
    ) -> bool:
        """Invalidate a recorded approval when final checks make its plan unusable."""

        if item.work.mode is not WorkflowMode.EXECUTION:
            return False
        state_repository = ExecutionStateRepository()
        loaded = state_repository.load(item.work.id)
        if loaded is None:
            return False
        record, ledger = loaded
        if record.state is not Lifecycle.APPROVED:
            return False
        cancelled, _ = ExecutionService(state_writer=state_repository).cancel_before_dispatch(
            record,
            ledger,
            reason=reason,
        )
        self._record_event(
            item,
            stage="execution",
            event_type="lifecycle_transition",
            status=cancelled.state.value,
            reason_code=reason,
            occurred_at=now,
            references={"execution_id": str(item.work.id)},
        )
        return cancelled.state is Lifecycle.CANCELLED

    def _record_workflow(
        self,
        item: QueuedWorkItem,
        workflow_result,
        *,
        occurred_at: datetime | None = None,
    ) -> None:
        """Persist only safe transition metadata for the administrator read model."""

        event_time = occurred_at or datetime.now(UTC)
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
                    occurred_at=event_time,
                    engine=item.work.engine,
                    mode=item.work.mode.value,
                    stage=transition.stage.value,
                    event_type=transition.kind.value,
                    status=transition.decision.value,
                    reason_code=transition.reason,
                    level=level,
                    references={
                        "work_id": str(item.work.id),
                        "subprocess_id": str(workflow_result.request_id),
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
            if status in {"recheck", "partial", "not_ready"}
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
        ledger_repository = SimulationPortfolioLedgerRepository()
        initial_ledger = PortfolioLedger(
            balance=PortfolioBalance(
                mode="simulation",
                currency=item.request.currency,
                available=Decimal("100"),
            )
        )
        simulation_ledger = ledger_repository.load_or_create(initial_ledger)
        runner = WorkflowSimulationRunner(
            report_store=PostgresSimulationReportStore(),
            mode_request_handlers=self._mode_request_handlers,
            simulation_ledger=simulation_ledger,
            ledger_writer=ledger_repository.merge,
        )
        result = runner.run(
            WorkflowSimulationRequest(
                config=SimulationRunConfig(
                    engine=engine,
                    starting_capital=simulation_ledger.balance.available,
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
            self._record_workflow(item, workflow_result, occurred_at=now)

        self._record_event(
            item,
            stage="simulation",
            event_type="lifecycle",
            status=result.simulation_result.status.value,
            occurred_at=now,
            references=(
                {"report_id": str(runner.last_report.run_id)} if runner.last_report else {}
            ),
        )
        if runner.last_ledger is not None:
            self._record_ledger_transitions(
                item,
                simulation_ledger,
                runner.last_ledger,
                dispatch_prefix=str(result.correlation_id),
                occurred_at=now,
            )

    @staticmethod
    def _execution_proposal(item: QueuedWorkItem) -> ExecutionProposal:
        capital, payout = valuation(item.request)
        return ExecutionProposal(
            work=item.work,
            request=item.request,
            expires_at=item.expires_at,
            currency=item.request.currency,
            capital_required=capital,
            payout=payout,
        )

    def _execution_state(
        self,
        item: QueuedWorkItem,
    ) -> tuple[ExecutionStateRepository, ExecutionRecord, PortfolioLedger]:
        state_repository = ExecutionStateRepository()
        initial_ledger = PortfolioLedger(
            balance=PortfolioBalance(
                mode="execution",
                currency=item.request.currency,
                available=Decimal("1000"),
            )
        )
        record, ledger = state_repository.load_or_create(
            ExecutionRecord(proposal=self._execution_proposal(item)),
            initial_ledger,
        )
        return state_repository, record, ledger

    def _run_execution(
        self,
        item: QueuedWorkItem,
        *,
        state_repository: ExecutionStateRepository,
        record: ExecutionRecord,
        ledger: PortfolioLedger,
        now: datetime,
    ) -> ExecutionRecord:
        if record.state is Lifecycle.AWAITING_APPROVAL:
            raise ValueError("execution_approval_required")

        before_transition_count = len(record.transitions)
        persisted_record, persisted_ledger = ExecutionService(
            state_writer=state_repository
        ).execute_approved(record, ledger, now=now)

        references = {
            "execution_id": str(item.work.id),
            "ledger_mode": persisted_ledger.balance.mode,
        }
        for state in persisted_record.transitions[before_transition_count:]:
            self._record_event(
                item,
                stage="execution",
                event_type="lifecycle_transition",
                status=state.value,
                reason_code=(persisted_record.error if state is persisted_record.state else None),
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
        return persisted_record

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
            if dispatch_prefix is not None and not command.dispatch_id.startswith(dispatch_prefix):
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
                stage=("settlement" if command.operation.value in {"settle", "fail"} else "ledger"),
                event_type="capital_transition",
                status=status,
                reason_code=command.operation.value,
                occurred_at=occurred_at,
                references={
                    "ledger_command_id": command.id,
                    "dispatch_id": command.dispatch_id,
                    "capital_amount": str(command.amount),
                    "ledger_position_state": (position.state if position is not None else status),
                    "available": str(balance.available),
                    "reserved": str(balance.reserved),
                    "locked": str(balance.locked),
                    "pending": str(balance.pending),
                    "settled": str(balance.settled),
                    "cost": str(balance.cost),
                },
            )
