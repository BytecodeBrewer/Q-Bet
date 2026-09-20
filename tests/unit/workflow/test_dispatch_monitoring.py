"""Focused Monitoring projections for durable dispatch outcomes."""

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


def _item(correlation_id: UUID):
    return SimpleNamespace(
        work=SimpleNamespace(
            id=UUID("87654321-4321-8765-4321-876543218765"),
            correlation_id=correlation_id,
            engine="bonus",
            mode=WorkflowMode.EXECUTION,
            opportunity_id="opportunity-1",
        )
    )


def test_ledger_transitions_include_reconstructable_capital_snapshots() -> None:
    writer = _MonitoringWriter()
    coordinator = ModeDispatchCoordinator(
        RoutingConfiguration(bonus=EngineModes(execution=True)),
        monitoring_writer=writer,
    )
    correlation_id = UUID("12345678-1234-5678-1234-567812345678")
    dispatch_id = "dispatch-1"
    before = PortfolioLedger(
        balance=PortfolioBalance(
            mode="execution",
            currency="EUR",
            available=Decimal("100"),
        )
    )
    after = before
    for operation, amount in (
        (LedgerOperation.RESERVE, Decimal("10")),
        (LedgerOperation.LOCK, Decimal("10")),
        (LedgerOperation.PENDING, Decimal("10")),
        (LedgerOperation.SETTLE, Decimal("12")),
    ):
        after, decision = after.apply(
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

    coordinator._record_ledger_transitions(
        _item(correlation_id),
        before,
        after,
        dispatch_id=dispatch_id,
        occurred_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
    )

    assert [record.reason_code for record in writer.records] == [
        "reserve",
        "lock",
        "pending",
        "settle",
    ]
    assert [record.status for record in writer.records] == [
        "reserved",
        "locked",
        "pending",
        "settled",
    ]
    assert writer.records[0].references["available"] == "90"
    assert writer.records[0].references["reserved"] == "10"
    assert writer.records[2].references["pending"] == "10"
    assert writer.records[-1].stage == "settlement"
    assert writer.records[-1].references["capital_amount"] == "12"
    assert writer.records[-1].references["available"] == "102"
    assert writer.records[-1].references["settled"] == "12"


def test_ledger_projection_ignores_commands_already_in_authoritative_snapshot() -> None:
    writer = _MonitoringWriter()
    coordinator = ModeDispatchCoordinator(
        RoutingConfiguration(bonus=EngineModes(execution=True)),
        monitoring_writer=writer,
    )
    correlation_id = UUID("12345678-1234-5678-1234-567812345678")
    dispatch_id = "dispatch-1"
    initial = PortfolioLedger(
        balance=PortfolioBalance(
            mode="execution",
            currency="EUR",
            available=Decimal("100"),
        )
    )
    reserved, decision = initial.apply(
        LedgerCommand(
            id=f"{dispatch_id}:reserve",
            dispatch_id=dispatch_id,
            correlation_id=str(correlation_id),
            currency="EUR",
            operation=LedgerOperation.RESERVE,
            amount=Decimal("10"),
        )
    )
    assert decision.accepted
    locked, decision = reserved.apply(
        LedgerCommand(
            id=f"{dispatch_id}:lock",
            dispatch_id=dispatch_id,
            correlation_id=str(correlation_id),
            currency="EUR",
            operation=LedgerOperation.LOCK,
            amount=Decimal("10"),
        )
    )
    assert decision.accepted

    coordinator._record_ledger_transitions(
        _item(correlation_id),
        reserved,
        locked,
        dispatch_id=dispatch_id,
        occurred_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
    )

    assert len(writer.records) == 1
    assert writer.records[0].reason_code == "lock"

def test_ledger_projection_recovers_lifecycle_order_after_jsonb_command_reordering() -> None:
    writer = _MonitoringWriter()
    coordinator = ModeDispatchCoordinator(
        RoutingConfiguration(bonus=EngineModes(execution=True)),
        monitoring_writer=writer,
    )
    correlation_id = UUID("12345678-1234-5678-1234-567812345678")
    dispatch_id = "dispatch-1"
    before = PortfolioLedger(
        balance=PortfolioBalance(
            mode="execution",
            currency="EUR",
            available=Decimal("100"),
        )
    )
    after = before
    for operation in (
        LedgerOperation.RESERVE,
        LedgerOperation.LOCK,
        LedgerOperation.PENDING,
    ):
        after, decision = after.apply(
            LedgerCommand(
                id=f"{dispatch_id}:{operation.value}",
                dispatch_id=dispatch_id,
                correlation_id=str(correlation_id),
                currency="EUR",
                operation=operation,
                amount=Decimal("10"),
            )
        )
        assert decision.accepted

    reordered = after.model_copy(
        update={"commands": dict(reversed(tuple(after.commands.items())))}
    )

    coordinator._record_ledger_transitions(
        _item(correlation_id),
        before,
        reordered,
        dispatch_id=dispatch_id,
        occurred_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
    )

    assert [record.reason_code for record in writer.records] == [
        "reserve",
        "lock",
        "pending",
    ]

