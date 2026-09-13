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
from qbet.workflow.queue import WorkState


@dataclass(frozen=True)
class PendingExecutionApproval:
    """Customer-safe projection of one approval decision."""

    execution_id: UUID
    engine: str
    opportunity_id: str
    capital_required: Decimal
    currency: str
    expires_at: datetime


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

    def pending_for(self, owner: str) -> tuple[PendingExecutionApproval, ...]:
        records = self._record_repository.list_awaiting_approval(owner=owner)
        return tuple(self._summary(record) for record in records)

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

    @staticmethod
    def _summary(record: ExecutionRecord) -> PendingExecutionApproval:
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
        )
