"""Human-in-the-loop Phase 3 execution confirmation boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from django.db import transaction

from qbet.execution.models import (
    ExecutionRecord,
    Lifecycle,
    ManualExecutionDecision,
)
from qbet.execution.service import ExecutionService
from qbet.monitoring import MonitoringLevel, MonitoringRecord
from qbet.notifications.models import ExecutionNotificationInstruction
from qbet.notifications.service import execution_instructions
from qbet.storage.ledger import (
    ExecutionRecordRepository,
    ExecutionStateRepository,
    ModeWorkQueueRepository,
)
from qbet.storage.monitoring import MonitoringPersistenceError, PostgresMonitoringRepository
from qbet.workflow.queue import QueuedWorkItem, WorkState


@dataclass(frozen=True)
class PendingManualExecution:
    execution_id: UUID
    correlation_id: UUID
    opportunity_id: str
    engine: str
    event_id: str
    sport: str | None
    market: str | None
    instructions: tuple[ExecutionNotificationInstruction, ...]
    action_starts_at: datetime
    action_deadline: datetime
    state: Lifecycle
    problem_note: str


class ManualExecutionService:
    """Expose and confirm manual provider actions without provider-side automation."""

    def __init__(
        self,
        *,
        state_repository: ExecutionStateRepository | None = None,
        record_repository: ExecutionRecordRepository | None = None,
        queue_repository: ModeWorkQueueRepository | None = None,
        monitoring_writer: PostgresMonitoringRepository | None = None,
    ) -> None:
        self._state_repository = state_repository or ExecutionStateRepository()
        self._record_repository = record_repository or ExecutionRecordRepository()
        self._queue_repository = queue_repository or ModeWorkQueueRepository()
        self._monitoring_writer = monitoring_writer or PostgresMonitoringRepository()

    def pending_for(
        self,
        owner: str,
        *,
        now: datetime | None = None,
    ) -> tuple[PendingManualExecution, ...]:
        observed_at = now or datetime.now(UTC)
        actions: list[PendingManualExecution] = []
        for record in self._record_repository.list_manual_action_pending(owner=owner):
            queued = self._queue_repository.load(record.proposal.work.id)
            if queued is None:
                continue
            if observed_at >= record.proposal.expires_at:
                self._expire(record, queued, owner=owner, now=observed_at)
                continue
            actions.append(self._projection(record, queued))
        return tuple(actions)

    def expire_due(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[ExecutionRecord, ...]:
        """Expire silent manual actions from a scheduler/background boundary."""

        observed_at = now or datetime.now(UTC)
        expired_records: list[ExecutionRecord] = []
        for record in self._record_repository.list_manual_action_pending():
            if observed_at < record.proposal.expires_at:
                continue
            queued = self._queue_repository.load(record.proposal.work.id)
            if queued is None:
                raise ValueError("execution_queue_item_missing")
            owner = record.proposal.work.owner
            if owner is None:
                raise ValueError("proposal_owner_required")
            expired = self._expire(record, queued, owner=owner, now=observed_at)
            if expired.state is Lifecycle.CANCELLED:
                expired_records.append(expired)
        return tuple(expired_records)

    def confirm(
        self,
        execution_id: UUID,
        *,
        actor: str,
        decision: ManualExecutionDecision,
        note: str = "",
        now: datetime | None = None,
    ) -> ExecutionRecord:
        observed_at = now or datetime.now(UTC)
        with transaction.atomic():
            loaded = self._state_repository.load(execution_id)
            if loaded is None:
                raise KeyError(execution_id)
            record, ledger = loaded
            owner = record.proposal.work.owner
            if owner is None or actor != owner:
                raise PermissionError("proposal_owner_required")
            queued = self._queue_repository.load(execution_id)
            if queued is None:
                raise ValueError("execution_queue_item_missing")

            previous_state = record.state
            confirmed, _ = ExecutionService(
                state_writer=self._state_repository
            ).confirm_manual_action(
                record,
                ledger,
                actor=actor,
                owner=owner,
                decision=decision,
                now=observed_at,
                note=note,
            )

            if confirmed.state is Lifecycle.CANCELLED:
                if queued.state in {WorkState.PENDING, WorkState.RECHECK}:
                    self._queue_repository.cancel(
                        execution_id,
                        now=observed_at,
                        reason=confirmed.error or "manual_action_not_performed",
                    )
                elif queued.state is WorkState.PROCESSING:
                    self._queue_repository.save(
                        queued.transition(
                            WorkState.CANCELLED,
                            now=observed_at,
                            reason=confirmed.error or "manual_action_not_performed",
                        )
                    )

            if confirmed.state is not previous_state:
                self._emit_confirmation(confirmed, decision=decision, now=observed_at)
            return confirmed

    def _expire(
        self,
        record: ExecutionRecord,
        queued: QueuedWorkItem,
        *,
        owner: str,
        now: datetime,
    ) -> ExecutionRecord:
        if record.proposal.work.owner != owner:
            raise PermissionError("proposal_owner_required")
        with transaction.atomic():
            loaded = self._state_repository.load(record.proposal.work.id)
            if loaded is None:
                raise ValueError("execution_state_missing")
            current, ledger = loaded
            if queued.state not in {WorkState.PENDING, WorkState.RECHECK}:
                return current
            expired, _ = ExecutionService(
                state_writer=self._state_repository
            ).expire_manual_action(current, ledger, now=now)
            if expired.state is Lifecycle.CANCELLED:
                self._queue_repository.cancel(
                    record.proposal.work.id,
                    now=now,
                    reason="manual_action_window_expired",
                )
            if expired.state is not current.state:
                self._emit(
                    expired,
                    status=expired.state.value,
                    reason_code="manual_action_window_expired",
                    now=now,
                )
            return expired

    @staticmethod
    def _projection(
        record: ExecutionRecord,
        queued: QueuedWorkItem,
    ) -> PendingManualExecution:
        revalidation = queued.market_revalidation
        last = record.manual_confirmations[-1] if record.manual_confirmations else None
        return PendingManualExecution(
            execution_id=record.proposal.work.id,
            correlation_id=record.proposal.work.correlation_id,
            opportunity_id=record.proposal.work.opportunity_id,
            engine=(
                "BonusEngine"
                if record.proposal.work.engine == "bonus"
                else "SportsCapitalEngine"
            ),
            event_id=(
                revalidation.event_id
                if revalidation is not None
                else record.proposal.work.opportunity_id
            ),
            sport=revalidation.sport if revalidation is not None else None,
            market=revalidation.market if revalidation is not None else None,
            instructions=execution_instructions(record, queued),
            action_starts_at=queued.scheduled_for,
            action_deadline=min(record.proposal.expires_at, queued.expires_at),
            state=record.state,
            problem_note=(
                last.note
                if last is not None and last.decision is ManualExecutionDecision.PROBLEM
                else ""
            ),
        )

    def _emit_confirmation(
        self,
        record: ExecutionRecord,
        *,
        decision: ManualExecutionDecision,
        now: datetime,
    ) -> None:
        reason = {
            ManualExecutionDecision.DONE: "manual_action_user_attested",
            ManualExecutionDecision.NOT_DONE: "manual_action_not_performed",
            ManualExecutionDecision.PROBLEM: "manual_action_problem",
        }[decision]
        self._emit(record, status=record.state.value, reason_code=reason, now=now)

    def _emit(
        self,
        record: ExecutionRecord,
        *,
        status: str,
        reason_code: str,
        now: datetime,
    ) -> None:
        try:
            self._monitoring_writer.append(
                MonitoringRecord(
                    correlation_id=record.proposal.work.correlation_id,
                    occurred_at=now,
                    engine=record.proposal.work.engine,
                    mode="execution",
                    stage="execution",
                    event_type="manual_action_confirmation",
                    status=status,
                    reason_code=reason_code,
                    level=(
                        MonitoringLevel.WARNING
                        if status in {
                            Lifecycle.ACTION_PROBLEM.value,
                            Lifecycle.CANCELLED.value,
                        }
                        else MonitoringLevel.INFO
                    ),
                    references={
                        "execution_id": str(record.proposal.work.id),
                        "work_id": str(record.proposal.work.id),
                        "opportunity_id": record.proposal.work.opportunity_id,
                    },
                )
            )
        except MonitoringPersistenceError:
            # Monitoring is observational and cannot roll back authoritative state.
            pass
