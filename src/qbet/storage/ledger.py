"""PostgreSQL repository for capital and approval lifecycle snapshots."""

from uuid import UUID

from django.db import DatabaseError, IntegrityError, transaction
from pydantic import ValidationError

from qbet.execution.models import ExecutionRecord, Lifecycle
from qbet.ledger import PortfolioLedger
from qbet.storage.models import (
    ExecutionRecordRow,
    ModeWorkQueueRow,
    PortfolioLedgerRow,
    RoutingConfigurationRow,
)
from qbet.workflow.models import WorkflowMode
from qbet.workflow.queue import QueuedWorkItem, WorkState
from qbet.workflow.routing import RoutingConfiguration, V1Engine, engine_modes


class AuthoritativePersistenceError(RuntimeError):
    """Raised when the configured operational PostgreSQL state cannot be used."""


class AuthoritativeStateConflict(ValueError):
    """Raised when a stale or conflicting snapshot would overwrite newer state."""


class RoutingConfigurationPersistenceError(RuntimeError):
    """Raised when durable engine/mode routing configuration is unavailable or invalid."""


def _validate_record_progress(stored: ExecutionRecord, incoming: ExecutionRecord) -> None:
    if stored.proposal != incoming.proposal:
        raise AuthoritativeStateConflict("execution_record_conflict")

    stored_transitions = stored.transitions
    incoming_transitions = incoming.transitions
    if (
        len(incoming_transitions) < len(stored_transitions)
        or incoming_transitions[: len(stored_transitions)] != stored_transitions
    ):
        raise AuthoritativeStateConflict("stale_execution_record")

    if len(incoming_transitions) == len(stored_transitions) and incoming != stored:
        raise AuthoritativeStateConflict("execution_record_conflict")


def _validate_ledger_progress(stored: PortfolioLedger, incoming: PortfolioLedger) -> None:
    if (
        stored.balance.mode != incoming.balance.mode
        or stored.balance.currency != incoming.balance.currency
    ):
        raise AuthoritativeStateConflict("portfolio_ledger_context_conflict")

    for command_id, command in stored.commands.items():
        if incoming.commands.get(command_id) != command:
            raise AuthoritativeStateConflict("stale_portfolio_ledger")

    if len(incoming.commands) == len(stored.commands) and incoming != stored:
        raise AuthoritativeStateConflict("portfolio_ledger_conflict")


class ExecutionStateRepository:
    """Atomically restores and persists the coupled execution and ledger state."""

    def load_or_create(
        self,
        record: ExecutionRecord,
        ledger: PortfolioLedger,
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        try:
            with transaction.atomic():
                balance = ledger.balance
                ledger_row, _ = PortfolioLedgerRow.objects.select_for_update().get_or_create(
                    mode=balance.mode,
                    currency=balance.currency,
                    defaults={"payload": ledger.model_dump(mode="json")},
                )
                restored_ledger = PortfolioLedger.model_validate(ledger_row.payload)
                proposal = record.proposal
                record_row = (
                    ExecutionRecordRow.objects.select_for_update()
                    .filter(record_id=proposal.work.id)
                    .first()
                )
                if record_row is None:
                    ExecutionRecordRow.objects.create(
                        record_id=proposal.work.id,
                        correlation_id=proposal.work.correlation_id,
                        mode=proposal.work.mode.value,
                        state=record.state.value,
                        payload=record.model_dump(mode="json"),
                    )
                    return record, restored_ledger
                restored_record = ExecutionRecord.model_validate(record_row.payload)
                if restored_record.proposal != proposal:
                    raise AuthoritativeStateConflict("execution_record_conflict")
                return restored_record, restored_ledger
        except DatabaseError as error:
            raise AuthoritativePersistenceError(
                "authoritative_execution_state_unavailable"
            ) from error

    def load(self, record_id: UUID) -> tuple[ExecutionRecord, PortfolioLedger] | None:
        """Restore one persisted execution together with its authoritative ledger."""

        try:
            with transaction.atomic():
                row = (
                    ExecutionRecordRow.objects.select_for_update()
                    .filter(record_id=record_id)
                    .first()
                )
                if row is None:
                    return None
                record = ExecutionRecord.model_validate(row.payload)
                ledger_row = PortfolioLedgerRow.objects.select_for_update().get(
                    mode=record.proposal.work.mode.value,
                    currency=record.proposal.currency,
                )
                return record, PortfolioLedger.model_validate(ledger_row.payload)
        except DatabaseError as error:
            raise AuthoritativePersistenceError(
                "authoritative_execution_state_unavailable"
            ) from error

    def persist(
        self, record: ExecutionRecord, ledger: PortfolioLedger
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        """Store a monotonic execution/ledger pair in one database transaction."""

        try:
            with transaction.atomic():
                balance = ledger.balance
                ledger_row = PortfolioLedgerRow.objects.select_for_update().get(
                    mode=balance.mode, currency=balance.currency
                )
                record_row = ExecutionRecordRow.objects.select_for_update().get(
                    record_id=record.proposal.work.id
                )
                if record_row.mode != record.proposal.work.mode.value:
                    raise AuthoritativeStateConflict("execution_record_mode_conflict")

                stored_ledger = PortfolioLedger.model_validate(ledger_row.payload)
                stored_record = ExecutionRecord.model_validate(record_row.payload)
                _validate_record_progress(stored_record, record)
                _validate_ledger_progress(stored_ledger, ledger)

                if stored_record == record and stored_ledger == ledger:
                    return stored_record, stored_ledger

                ledger_row.payload = ledger.model_dump(mode="json")
                ledger_row.save(update_fields=("payload", "updated_at"))
                record_row.correlation_id = record.proposal.work.correlation_id
                record_row.state = record.state.value
                record_row.payload = record.model_dump(mode="json")
                record_row.save(
                    update_fields=("correlation_id", "state", "payload", "updated_at")
                )
                return record, ledger
        except DatabaseError as error:
            raise AuthoritativePersistenceError(
                "authoritative_execution_state_unavailable"
            ) from error


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

    def list_awaiting_approval(self, *, owner: str) -> tuple[ExecutionRecord, ...]:
        rows = ExecutionRecordRow.objects.filter(
            state=Lifecycle.AWAITING_APPROVAL.value
        ).order_by("updated_at")
        records = tuple(ExecutionRecord.model_validate(row.payload) for row in rows)
        return tuple(record for record in records if record.proposal.work.owner == owner)

    @transaction.atomic
    def save_transition(self, record: ExecutionRecord) -> ExecutionRecord:
        return self.save(record)


class RoutingConfigurationRepository:
    def save(self, configuration: RoutingConfiguration) -> RoutingConfiguration:
        try:
            RoutingConfigurationRow.objects.update_or_create(
                pk=1,
                defaults={"payload": configuration.model_dump(mode="json")},
            )
        except DatabaseError as error:
            raise RoutingConfigurationPersistenceError(
                "routing configuration is unavailable"
            ) from error
        return configuration

    def load(self) -> RoutingConfiguration | None:
        try:
            row = RoutingConfigurationRow.objects.filter(pk=1).first()
            if row is None:
                return None
            return RoutingConfiguration.model_validate(row.payload)
        except (DatabaseError, ValidationError) as error:
            raise RoutingConfigurationPersistenceError(
                "routing configuration is unavailable"
            ) from error

    def set_mode_active(
        self,
        *,
        engine: V1Engine,
        mode: WorkflowMode,
        active: bool,
    ) -> RoutingConfiguration:
        """Atomically toggle one engine/mode runtime route without touching the sibling mode."""

        try:
            with transaction.atomic():
                row, _ = RoutingConfigurationRow.objects.select_for_update().get_or_create(
                    pk=1,
                    defaults={"payload": RoutingConfiguration().model_dump(mode="json")},
                )
                current = RoutingConfiguration.model_validate(row.payload)
                modes = engine_modes(current, engine)
                updated_modes = modes.model_copy(update={mode.value: active})
                updated = current.model_copy(update={engine: updated_modes})
                row.payload = updated.model_dump(mode="json")
                row.save(update_fields=("payload", "updated_at"))
                return updated
        except (DatabaseError, ValidationError) as error:
            raise RoutingConfigurationPersistenceError(
                "routing configuration is unavailable"
            ) from error


class ModeWorkQueueRepository:
    """Persistence boundary for independent Simulation and Execution queues."""

    def enqueue(self, item: QueuedWorkItem) -> QueuedWorkItem:
        try:
            with transaction.atomic():
                ModeWorkQueueRow.objects.create(
                    work_id=item.work.id,
                    correlation_id=item.work.correlation_id,
                    mode=item.work.mode.value,
                    state=item.state.value,
                    scheduled_for=item.scheduled_for,
                    payload=item.model_dump(mode="json"),
                )
                return item
        except IntegrityError:
            existing = self.load(item.work.id)
            if existing is None or existing.work != item.work or existing.request != item.request:
                raise ValueError("work_id already belongs to a different dispatch")
            return existing

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

    @transaction.atomic
    def claim_due(self, now) -> tuple[QueuedWorkItem, ...]:
        """Atomically claim due work so concurrent workers cannot dispatch it twice."""

        rows = ModeWorkQueueRow.objects.select_for_update(skip_locked=True).filter(
            scheduled_for__lte=now, state=WorkState.PENDING.value
        )
        claimed: list[QueuedWorkItem] = []
        for row in rows:
            item = QueuedWorkItem.model_validate(row.payload)
            processing = item.transition(WorkState.PROCESSING, now=now)
            row.state = processing.state.value
            row.payload = processing.model_dump(mode="json")
            row.save(update_fields=("state", "payload", "updated_at"))
            claimed.append(processing)
        return tuple(claimed)

    def reschedule(self, work_id: UUID, *, scheduled_for, now) -> QueuedWorkItem:
        with transaction.atomic():
            row = ModeWorkQueueRow.objects.select_for_update().get(work_id=work_id)
            item = QueuedWorkItem.model_validate(row.payload).reschedule(
                scheduled_for=scheduled_for, now=now
            )
            row.state = item.state.value
            row.scheduled_for = item.scheduled_for
            row.payload = item.model_dump(mode="json")
            row.save(update_fields=("state", "scheduled_for", "payload", "updated_at"))
            return item

    def cancel(self, work_id: UUID, *, now, reason: str) -> QueuedWorkItem:
        """Cancel approval-waiting work idempotently without dispatching it."""

        with transaction.atomic():
            row = ModeWorkQueueRow.objects.select_for_update().get(work_id=work_id)
            item = QueuedWorkItem.model_validate(row.payload)
            if item.state is WorkState.CANCELLED:
                return item
            if item.state not in {WorkState.PENDING, WorkState.RECHECK}:
                raise ValueError("work_not_cancellable")
            cancelled = item.transition(WorkState.CANCELLED, now=now, reason=reason)
            row.state = cancelled.state.value
            row.payload = cancelled.model_dump(mode="json")
            row.save(update_fields=("state", "payload", "updated_at"))
            return cancelled
