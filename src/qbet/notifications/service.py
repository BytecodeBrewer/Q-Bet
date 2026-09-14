"""Idempotent notification orchestration for approved Execution tasks."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid5

from qbet.engines import BonusEngine, BonusEngineRequest, SportsCapitalEngine
from qbet.execution.models import ExecutionRecord, Lifecycle
from qbet.monitoring import MonitoringLevel, MonitoringRecord
from qbet.workflow.queue import QueuedWorkItem, WorkState

from .models import (
    ExecutionNotificationInstruction,
    ExecutionNotificationTask,
    NotificationOutcome,
    NotificationRecipient,
    NotificationStatus,
)


class NotificationDeliveryError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class NotificationRepository(Protocol):
    def load(self, task_id: UUID) -> ExecutionNotificationTask | None: ...

    def create(self, task: ExecutionNotificationTask) -> tuple[ExecutionNotificationTask, bool]: ...

    def save(self, task: ExecutionNotificationTask) -> ExecutionNotificationTask: ...


class NotificationTransport(Protocol):
    def send(self, task: ExecutionNotificationTask) -> None: ...


class MonitoringWriter(Protocol):
    def append(self, record: MonitoringRecord) -> MonitoringRecord: ...


class InMemoryNotificationRepository:
    """Deterministic repository used by unit tests and local composition tests."""

    def __init__(self) -> None:
        self._tasks: dict[UUID, ExecutionNotificationTask] = {}

    def load(self, task_id: UUID) -> ExecutionNotificationTask | None:
        return self._tasks.get(task_id)

    def create(self, task: ExecutionNotificationTask) -> tuple[ExecutionNotificationTask, bool]:
        existing = self._tasks.get(task.id)
        if existing is not None:
            return existing, False
        self._tasks[task.id] = task
        return task, True

    def save(self, task: ExecutionNotificationTask) -> ExecutionNotificationTask:
        self._tasks[task.id] = task
        return task


class ExecutionNotificationService:
    """Creates and delivers exactly one user notification per approved execution."""

    def __init__(
        self,
        *,
        repository: NotificationRepository,
        transport: NotificationTransport,
        monitoring_writer: MonitoringWriter | None = None,
    ) -> None:
        self._repository = repository
        self._transport = transport
        self._monitoring_writer = monitoring_writer

    def notify(
        self,
        record: ExecutionRecord,
        queued: QueuedWorkItem,
        recipient: NotificationRecipient,
        *,
        now: datetime,
    ) -> NotificationOutcome:
        reason = _eligibility_reason(record, queued, recipient, now)
        if reason is not None:
            return NotificationOutcome(accepted=False, reason_code=reason)

        task = _build_task(record, queued, recipient, now)
        persisted, created = self._repository.create(task)
        if not created:
            sendable = persisted.status in {
                NotificationStatus.QUEUED,
                NotificationStatus.SENT,
                NotificationStatus.ACKNOWLEDGED,
            }
            return NotificationOutcome(
                task=persisted,
                accepted=sendable,
                reason_code=(
                    None if sendable else persisted.failure_reason or "notification_not_sendable"
                ),
                duplicate=True,
            )

        queued_task = _transition(persisted, NotificationStatus.QUEUED, now=now)
        queued_task = self._repository.save(queued_task)
        self._emit(queued_task)

        try:
            self._transport.send(queued_task)
        except NotificationDeliveryError as error:
            failed = _transition(
                queued_task,
                NotificationStatus.FAILED,
                now=now,
                failure_reason=error.reason_code,
            )
            failed = self._repository.save(failed)
            self._emit(failed)
            return NotificationOutcome(
                task=failed,
                accepted=False,
                reason_code=error.reason_code,
            )
        except Exception:
            failed = _transition(
                queued_task,
                NotificationStatus.FAILED,
                now=now,
                failure_reason="email_delivery_failed",
            )
            failed = self._repository.save(failed)
            self._emit(failed)
            return NotificationOutcome(
                task=failed,
                accepted=False,
                reason_code="email_delivery_failed",
            )

        sent = _transition(queued_task, NotificationStatus.SENT, now=now)
        sent = self._repository.save(sent)
        self._emit(sent)
        return NotificationOutcome(task=sent, accepted=True)

    def acknowledge(
        self,
        task_id: UUID,
        *,
        recipient_id: str,
        now: datetime,
    ) -> NotificationOutcome:
        task = self._repository.load(task_id)
        if task is None:
            return NotificationOutcome(accepted=False, reason_code="notification_not_found")
        if task.recipient.user_id != recipient_id:
            return NotificationOutcome(
                task=task,
                accepted=False,
                reason_code="notification_recipient_mismatch",
            )
        if task.status is NotificationStatus.ACKNOWLEDGED:
            return NotificationOutcome(task=task, accepted=True, duplicate=True)
        if task.status is not NotificationStatus.SENT:
            return NotificationOutcome(
                task=task,
                accepted=False,
                reason_code="notification_not_acknowledgeable",
            )
        if now < task.lifecycle_at:
            return NotificationOutcome(
                task=task,
                accepted=False,
                reason_code="notification_time_reversed",
            )
        if now >= task.action_deadline:
            expired = _transition(task, NotificationStatus.EXPIRED, now=now)
            expired = self._repository.save(expired)
            self._emit(expired)
            return NotificationOutcome(
                task=expired,
                accepted=False,
                reason_code="notification_expired",
            )
        acknowledged = _transition(task, NotificationStatus.ACKNOWLEDGED, now=now)
        acknowledged = self._repository.save(acknowledged)
        self._emit(acknowledged)
        return NotificationOutcome(task=acknowledged, accepted=True)

    def _emit(self, task: ExecutionNotificationTask) -> None:
        writer = self._monitoring_writer
        if writer is None:
            return
        level = (
            MonitoringLevel.ERROR
            if task.status is NotificationStatus.FAILED
            else MonitoringLevel.WARNING
            if task.status is NotificationStatus.EXPIRED
            else MonitoringLevel.INFO
        )
        try:
            writer.append(
                MonitoringRecord(
                    correlation_id=task.correlation_id,
                    occurred_at=task.lifecycle_at,
                    engine=task.engine,
                    mode="execution",
                    stage="notification",
                    event_type="notification_state",
                    status=task.status.value,
                    reason_code=task.failure_reason,
                    level=level,
                    references={
                        "notification_id": str(task.id),
                        "execution_id": str(task.execution_id),
                        "recipient_id": task.recipient.user_id,
                        "opportunity_id": task.opportunity_id,
                    },
                )
            )
        except Exception:
            # Monitoring is observational and must never roll back authoritative notification state.
            return


def _eligibility_reason(
    record: ExecutionRecord,
    queued: QueuedWorkItem,
    recipient: NotificationRecipient,
    now: datetime,
) -> str | None:
    work = record.proposal.work
    if record.state is not Lifecycle.APPROVED or record.approval is None:
        return "execution_not_approved"
    if work.mode.value != "execution":
        return "execution_mode_required"
    if work.owner is None:
        return "execution_not_assigned"
    if queued.work.id != work.id or queued.work.correlation_id != work.correlation_id:
        return "execution_queue_mismatch"
    if queued.work.owner != work.owner or queued.request != record.proposal.request:
        return "execution_queue_mismatch"
    if queued.state not in {WorkState.PENDING, WorkState.RECHECK, WorkState.PROCESSING}:
        return "execution_not_notifiable"
    deadline = min(record.proposal.expires_at, queued.expires_at)
    action_window = deadline - queued.scheduled_for
    if not timedelta(minutes=20) <= action_window <= timedelta(minutes=50):
        return "notification_action_window_invalid"
    if now >= deadline:
        return "execution_expired"
    return None


def _build_task(
    record: ExecutionRecord,
    queued: QueuedWorkItem,
    recipient: NotificationRecipient,
    now: datetime,
) -> ExecutionNotificationTask:
    request = record.proposal.request
    evaluation = (
        BonusEngine().evaluate(request)
        if isinstance(request, BonusEngineRequest)
        else SportsCapitalEngine().evaluate(request)
    )
    instructions = tuple(
        ExecutionNotificationInstruction(
            provider=step.offer_id,
            offer_id=step.offer_id,
            amount=step.stake,
            currency=record.proposal.currency,
        )
        for step in evaluation.execution_plan.steps
    )
    work = record.proposal.work
    deadline = min(record.proposal.expires_at, queued.expires_at)
    return ExecutionNotificationTask(
        id=uuid5(work.id, recipient.user_id),
        execution_id=work.id,
        correlation_id=work.correlation_id,
        recipient=recipient,
        opportunity_id=work.opportunity_id,
        engine="BonusEngine" if work.engine == "bonus" else "SportsCapitalEngine",
        strategy=evaluation.strategy_result.strategy,
        instructions=instructions,
        action_starts_at=queued.scheduled_for,
        action_deadline=deadline,
        created_at=now,
        lifecycle_at=now,
    )


def _transition(
    task: ExecutionNotificationTask,
    status: NotificationStatus,
    *,
    now: datetime,
    failure_reason: str | None = None,
) -> ExecutionNotificationTask:
    if now < task.lifecycle_at:
        raise ValueError("notification lifecycle cannot move backwards")
    permitted = {
        NotificationStatus.PENDING: {
            NotificationStatus.QUEUED,
            NotificationStatus.FAILED,
            NotificationStatus.EXPIRED,
        },
        NotificationStatus.QUEUED: {
            NotificationStatus.SENT,
            NotificationStatus.FAILED,
            NotificationStatus.EXPIRED,
        },
        NotificationStatus.SENT: {
            NotificationStatus.ACKNOWLEDGED,
            NotificationStatus.EXPIRED,
        },
    }
    if status not in permitted.get(task.status, set()):
        raise ValueError(f"invalid notification transition: {task.status} -> {status}")

    values = task.model_dump(mode="python")
    values.update(
        {
            "status": status,
            "lifecycle_at": now,
            "failure_reason": failure_reason if status is NotificationStatus.FAILED else None,
            "sent_at": now if status is NotificationStatus.SENT else task.sent_at,
            "acknowledged_at": (
                now if status is NotificationStatus.ACKNOWLEDGED else task.acknowledged_at
            ),
        }
    )
    return ExecutionNotificationTask.model_validate(values)
