"""Persisted human approval boundary for deterministic Execution work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from django.db import transaction

from qbet.execution.models import ExecutionRecord, Lifecycle
from qbet.execution.service import ExecutionService
from qbet.monitoring import MonitoringLevel, MonitoringRecord
from qbet.storage.ledger import (
    ExecutionRecordRepository,
    ExecutionStateRepository,
    ModeWorkQueueRepository,
)
from qbet.storage.monitoring import PostgresMonitoringRepository
from qbet.workflow.queue import QueuedWorkItem, WorkState


@dataclass(frozen=True)
class PendingExecutionApproval:
    """Customer-safe projection of one approval decision."""

    execution_id: UUID
    engine: str
    opportunity_id: str
    capital_required: Decimal
    currency: str
    expires_at: datetime
    remaining_validity: str


class ExecutionApprovalService:
    """Coordinates a human decision with durable execution and queue state."""

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
    ) -> tuple[PendingExecutionApproval, ...]:
        observed_at = now or datetime.now(UTC)
        records = self._record_repository.list_awaiting_approval(owner=owner)
        approvals: list[PendingExecutionApproval] = []
        for record in records:
            if observed_at >= record.proposal.expires_at:
                self._reconcile_expired(
                    record.proposal.work.id,
                    owner=owner,
                    now=observed_at,
                )
                continue
            approvals.append(self._summary(record, now=observed_at))
        return tuple(approvals)

    def active_count_for(self, owner: str, *, now: datetime | None = None) -> int:
        """Return an owner-scoped count without causing write side effects."""

        observed_at = now or datetime.now(UTC)
        records = self._record_repository.list_awaiting_approval(owner=owner)
        return sum(record.proposal.expires_at > observed_at for record in records)

    def decide(
        self,
        execution_id: UUID,
        *,
        actor: str,
        approve: bool,
        now: datetime | None = None,
        notification_email: str = "",
        notification_display_name: str = "",
    ) -> ExecutionRecord:
        decision_time = now or datetime.now(UTC)
        with transaction.atomic():
            loaded = self._state_repository.load(execution_id)
            if loaded is None:
                raise KeyError(execution_id)
            record, ledger = loaded
            owner = record.proposal.work.owner
            if owner is None or actor != owner:
                raise PermissionError("proposal_owner_required")

            queue_item = self._queue_repository.load(execution_id)
            if queue_item is None:
                raise ValueError("execution_queue_item_missing")

            if (
                record.state is Lifecycle.AWAITING_APPROVAL
                and decision_time >= record.proposal.expires_at
            ):
                return self._expire_locked(
                    record,
                    ledger,
                    queue_item,
                    now=decision_time,
                )

            previous_state = record.state
            decided, _ = ExecutionService(
                state_writer=self._state_repository
            ).record_decision(
                record,
                ledger,
                actor=actor,
                owner=owner,
                approve=approve,
                now=decision_time,
                notification_email=notification_email,
                notification_display_name=notification_display_name,
            )

            if decided.state is Lifecycle.APPROVED:
                if queue_item.state is WorkState.RECHECK:
                    self._queue_repository.reschedule(
                        execution_id,
                        scheduled_for=decision_time,
                        now=decision_time,
                    )
            elif decided.state is Lifecycle.REJECTED:
                if queue_item.state in {WorkState.PENDING, WorkState.RECHECK}:
                    self._queue_repository.cancel(
                        execution_id,
                        now=decision_time,
                        reason="execution_rejected_by_user",
                    )

            if decided.state is not previous_state:
                self._monitoring_writer.append(
                    MonitoringRecord(
                        correlation_id=decided.proposal.work.correlation_id,
                        occurred_at=decision_time,
                        engine=decided.proposal.work.engine,
                        mode="execution",
                        stage="execution",
                        event_type="lifecycle_transition",
                        status=decided.state.value,
                        reason_code=(
                            "user_approved"
                            if decided.state is Lifecycle.APPROVED
                            else decided.error or "user_rejected"
                        ),
                        level=(
                            MonitoringLevel.INFO
                            if decided.state is Lifecycle.APPROVED
                            else MonitoringLevel.WARNING
                        ),
                        references={
                            "work_id": str(decided.proposal.work.id),
                            "opportunity_id": decided.proposal.work.opportunity_id,
                        },
                    )
                )
            return decided

    def _reconcile_expired(
        self,
        execution_id: UUID,
        *,
        owner: str,
        now: datetime,
    ) -> ExecutionRecord:
        with transaction.atomic():
            loaded = self._state_repository.load(execution_id)
            if loaded is None:
                raise ValueError("execution_state_missing")
            record, ledger = loaded
            if record.proposal.work.owner != owner:
                raise PermissionError("proposal_owner_required")
            if (
                record.state is not Lifecycle.AWAITING_APPROVAL
                or now < record.proposal.expires_at
            ):
                return record

            queue_item = self._queue_repository.load(execution_id)
            if queue_item is None:
                raise ValueError("execution_queue_item_missing")
            return self._expire_locked(record, ledger, queue_item, now=now)

    def _expire_locked(
        self,
        record: ExecutionRecord,
        ledger,
        queue_item: QueuedWorkItem,
        *,
        now: datetime,
    ) -> ExecutionRecord:
        """Fail closed through existing execution and queue terminal states."""

        if record.state is not Lifecycle.AWAITING_APPROVAL:
            return record
        if now < record.proposal.expires_at:
            return record
        if queue_item.state not in {
            WorkState.PENDING,
            WorkState.RECHECK,
            WorkState.CANCELLED,
            WorkState.EXPIRED,
        }:
            raise ValueError("approval_expiry_queue_not_terminalizable")

        cancelled, _ = ExecutionService(
            state_writer=self._state_repository
        ).cancel_before_dispatch(
            record,
            ledger,
            reason="approval_expired",
        )
        if queue_item.state in {WorkState.PENDING, WorkState.RECHECK}:
            self._queue_repository.cancel(
                record.proposal.work.id,
                now=now,
                reason="approval_expired",
            )
        return cancelled

    @staticmethod
    def _summary(record: ExecutionRecord, *, now: datetime) -> PendingExecutionApproval:
        proposal = record.proposal
        return PendingExecutionApproval(
            execution_id=proposal.work.id,
            engine=(
                "BonusEngine"
                if proposal.work.engine == "bonus"
                else "SportsCapitalEngine"
            ),
            opportunity_id=proposal.work.opportunity_id,
            capital_required=proposal.capital_required,
            currency=proposal.currency,
            expires_at=proposal.expires_at,
            remaining_validity=ExecutionApprovalService._remaining_validity(
                proposal.expires_at,
                now,
            ),
        )

    @staticmethod
    def _remaining_validity(expires_at: datetime, now: datetime) -> str:
        remaining = max(0, int((expires_at - now).total_seconds()))
        hours, remainder = divmod(remaining, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"
