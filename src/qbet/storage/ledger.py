"""PostgreSQL repository for capital and approval lifecycle snapshots."""

from uuid import UUID

from django.db import transaction

from qbet.execution.models import ExecutionRecord
from qbet.ledger import PortfolioLedger
from qbet.storage.models import ExecutionRecordRow, PortfolioLedgerRow
from qbet.storage.models import RoutingConfigurationRow
from qbet.workflow.routing import RoutingConfiguration


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
