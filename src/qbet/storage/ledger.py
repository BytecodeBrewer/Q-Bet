"""PostgreSQL repository for capital and approval lifecycle snapshots."""

from uuid import UUID

from django.db import transaction

from qbet.execution.models import ExecutionRecord
from qbet.ledger import PortfolioLedger
from qbet.storage.models import ExecutionRecordRow, ModeWorkQueueRow, PortfolioLedgerRow
from qbet.storage.models import RoutingConfigurationRow
from qbet.workflow.routing import RoutingConfiguration
from qbet.workflow.queue import QueuedWorkItem, WorkState


class PortfolioLedgerRepository:
    def save(self, ledger: PortfolioLedger) -> PortfolioLedger:
        balance = ledger.balance
        PortfolioLedgerRow.objects.update_or_create(
            mode=balance.mode,
            currency=balance.currency,
            defaults={"payload": ledger.model_dump(mode="json")},
        )
        return ledger

    def load(self, *, mode: str, currency: str) -> PortfolioLedger | None:
        row = PortfolioLedgerRow.objects.filter(mode=mode, currency=currency).first()
        if row is None:
            return None
        return PortfolioLedger.model_validate(row.payload)

    @transaction.atomic
    def apply(self, ledger: PortfolioLedger) -> PortfolioLedger:
        """Persist one already-validated immutable transition atomically."""

        return self.save(ledger)


class ExecutionRecordRepository:
    def save(self, record: ExecutionRecord) -> ExecutionRecord:
        proposal = record.proposal
        ExecutionRecordRow.objects.update_or_create(
            record_id=proposal.work.id,
            defaults={
                "correlation_id": proposal.work.correlation_id,
                "mode": proposal.work.mode.value,
                "state": record.state.value,
                "payload": record.model_dump(mode="json"),
            },
        )
        return record

    def load(self, record_id: UUID) -> ExecutionRecord | None:
        row = ExecutionRecordRow.objects.filter(record_id=record_id).first()
        if row is None:
            return None
        return ExecutionRecord.model_validate(row.payload)

    @transaction.atomic
    def save_transition(self, record: ExecutionRecord) -> ExecutionRecord:
        return self.save(record)


class RoutingConfigurationRepository:
    def save(self, configuration: RoutingConfiguration) -> RoutingConfiguration:
        RoutingConfigurationRow.objects.update_or_create(
            pk=1,
            defaults={"payload": configuration.model_dump(mode="json")},
        )
        return configuration

    def load(self) -> RoutingConfiguration | None:
        row = RoutingConfigurationRow.objects.filter(pk=1).first()
        if row is None:
            return None
        return RoutingConfiguration.model_validate(row.payload)


class ModeWorkQueueRepository:
    """Persistence boundary for independent Simulation and Execution queues."""

    def enqueue(self, item: QueuedWorkItem) -> QueuedWorkItem:
        existing = self.load(item.work.id)
        if existing is not None:
            if existing.work != item.work or existing.request != item.request:
                raise ValueError("work_id already belongs to a different dispatch")
            return existing
        return self.save(item)

    def save(self, item: QueuedWorkItem) -> QueuedWorkItem:
        ModeWorkQueueRow.objects.update_or_create(
            work_id=item.work.id,
            defaults={
                "correlation_id": item.work.correlation_id,
                "mode": item.work.mode.value,
                "state": item.state.value,
                "scheduled_for": item.scheduled_for,
                "payload": item.model_dump(mode="json"),
            },
        )
        return item

    def load(self, work_id: UUID) -> QueuedWorkItem | None:
        row = ModeWorkQueueRow.objects.filter(work_id=work_id).first()
        return None if row is None else QueuedWorkItem.model_validate(row.payload)

    def due(self, now) -> tuple[QueuedWorkItem, ...]:
        rows = ModeWorkQueueRow.objects.filter(
            scheduled_for__lte=now,
            state=WorkState.PENDING.value,
        )
        return tuple(QueuedWorkItem.model_validate(row.payload) for row in rows)
