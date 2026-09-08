"""Monitoring projections for durable dispatch outcomes."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from qbet.domain.ledger import LedgerCommand, LedgerOperation, PortfolioBalance
from qbet.ledger import PortfolioLedger
from qbet.workflow import WorkflowMode
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.routing import EngineModes, RoutingConfiguration


class _MonitoringWriter:
    def __init__(self) -> None:
        self.records = []

    def append(self, record):
        self.records.append(record)
        return record


def test_ledger_transitions_are_projected_as_safe_monitoring_events() -> None:
    writer = _MonitoringWriter()
    coordinator = ModeDispatchCoordinator(
        RoutingConfiguration(bonus=EngineModes(execution=True)), monitoring_writer=writer
    )
    correlation_id = UUID("12345678-1234-5678-1234-567812345678")
    dispatch_id = "dispatch-1"
    ledger = PortfolioLedger(
        balance=PortfolioBalance(mode="execution", currency="EUR", available=Decimal("100"))
    )
    for operation, amount in (
        (LedgerOperation.RESERVE, Decimal("10")),
        (LedgerOperation.LOCK, Decimal("10")),
        (LedgerOperation.PENDING, Decimal("10")),
        (LedgerOperation.SETTLE, Decimal("12")),
    ):
        ledger, decision = ledger.apply(
            LedgerCommand(
                id=f"{dispatch_id}:{operation.value}",
                dispatch_id=dispatch_id,
                correlation_id=str(correlation_id),
                currency="EUR",
                operation=operation,
                amount=amount,
            )
        )
        assert decision.accepted

    item = SimpleNamespace(
        work=SimpleNamespace(
            id=UUID("87654321-4321-8765-4321-876543218765"),
            correlation_id=correlation_id,
            engine="bonus",
            mode=WorkflowMode.EXECUTION,
            opportunity_id="opportunity-1",
        )
    )

    coordinator._record_ledger_transitions(item, ledger, dispatch_id=dispatch_id)

    assert [record.reason_code for record in writer.records] == [
        "reserve",
        "lock",
        "pending",
        "settle",
    ]
    assert writer.records[-1].stage == "settlement"
    assert writer.records[-1].references["capital_amount"] == "12"
