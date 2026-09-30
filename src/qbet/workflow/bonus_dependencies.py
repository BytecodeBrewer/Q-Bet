"""Bonus Offer mutation invalidation across durable workflow and execution state."""

from __future__ import annotations

from datetime import datetime

from django.db import transaction

from qbet.execution.models import Lifecycle
from qbet.execution.service import ExecutionService
from qbet.storage.ledger import ExecutionStateRepository, ModeWorkQueueRepository
from qbet.workflow.models import WorkflowMode
from qbet.workflow.queue import QueuedWorkItem


class BonusOfferWorkInvalidator:
    """Invalidate pre-dispatch work derived from superseded promotion terms."""

    def __init__(
        self,
        *,
        queue_repository: ModeWorkQueueRepository | None = None,
        state_repository: ExecutionStateRepository | None = None,
    ) -> None:
        self._queue_repository = queue_repository or ModeWorkQueueRepository()
        self._state_repository = state_repository or ExecutionStateRepository()

    @transaction.atomic
    def invalidate(
        self,
        offer_id: int,
        *,
        current_version: int,
        now: datetime,
        removed: bool,
    ) -> tuple[QueuedWorkItem, ...]:
        items = self._queue_repository.invalidate_bonus_offer(
            offer_id,
            current_version=current_version,
            now=now,
            removed=removed,
        )
        reason = "bonus_offer_removed" if removed else "bonus_offer_version_changed"
        for item in items:
            if item.work.mode is not WorkflowMode.EXECUTION:
                continue
            loaded = self._state_repository.load(item.work.id)
            if loaded is None:
                continue
            record, ledger = loaded
            if record.state in {Lifecycle.AWAITING_APPROVAL, Lifecycle.APPROVED}:
                ExecutionService(
                    state_writer=self._state_repository
                ).cancel_before_dispatch(
                    record,
                    ledger,
                    reason=reason,
                )
                continue
            if record.state in {
                Lifecycle.REJECTED,
                Lifecycle.CANCELLED,
                Lifecycle.FAILED,
                Lifecycle.SETTLED,
            }:
                continue
            raise ValueError("bonus_offer_work_already_dispatched")
        return items
