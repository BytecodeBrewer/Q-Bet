"""Pure approval and dispatch transitions, committed atomically by storage."""

from datetime import datetime
from typing import Protocol

from qbet.domain.ledger import LedgerOperation
from qbet.execution.models import ApprovedExecutionRequest, ExecutionRecord, Lifecycle
from qbet.execution.sandbox import (
    BonusSandboxAdapter,
    SandboxRequestHandler,
    SportsCapitalSandboxAdapter,
)
from qbet.ledger import PortfolioLedger
from qbet.settlement import SettlementService, ledger_command, transition


class LedgerWriter(Protocol):
    def save(self, ledger: PortfolioLedger) -> PortfolioLedger: ...


class ExecutionWriter(Protocol):
    def save(self, record: ExecutionRecord) -> ExecutionRecord: ...


class ExecutionService:
    def __init__(
        self,
        *,
        ledger_writer: LedgerWriter | None = None,
        execution_writer: ExecutionWriter | None = None,
    ) -> None:
        self._ledger_writer = ledger_writer
        self._execution_writer = execution_writer

    def _persist(
        self, record: ExecutionRecord, ledger: PortfolioLedger
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        if self._ledger_writer is not None:
            ledger = self._ledger_writer.save(ledger)
        if self._execution_writer is not None:
            record = self._execution_writer.save(record)
        return record, ledger

    def decide(
        self,
        record: ExecutionRecord,
        ledger: PortfolioLedger,
        *,
        actor: str,
        owner: str,
        approve: bool,
        now: datetime,
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        if not actor or actor != owner:
            raise PermissionError("proposal_owner_required")
        if record.state is not Lifecycle.AWAITING_APPROVAL:
            return self._persist(record, ledger)
        if not approve:
            return self._persist(transition(record, Lifecycle.REJECTED), ledger)
        reason = SandboxRequestHandler().validate(record.proposal, now)
        if ledger.balance.mode != record.proposal.work.mode.value:
            reason = "ledger_mode_mismatch"
        if reason:
            return self._persist(transition(record, Lifecycle.REJECTED, error=reason), ledger)
        reserved, decision = ledger.apply(
            ledger_command(record, LedgerOperation.RESERVE, record.proposal.capital_required)
        )
        if not decision.accepted:
            return self._persist(
                transition(record, Lifecycle.REJECTED, error=decision.reason), ledger
            )
        approval = ApprovedExecutionRequest(
            proposal=record.proposal, approved_by=actor, approved_at=now
        )
        record = transition(record, Lifecycle.APPROVED, approval=approval)
        for operation, state in (
            (LedgerOperation.LOCK, Lifecycle.DISPATCHED),
            (LedgerOperation.PENDING, Lifecycle.ACKNOWLEDGED),
        ):
            reserved, decision = reserved.apply(
                ledger_command(record, operation, record.proposal.capital_required)
            )
            if not decision.accepted:
                raise ValueError(decision.reason)
            record = transition(record, state)
        adapter = (
            BonusSandboxAdapter()
            if record.proposal.work.engine == "bonus"
            else SportsCapitalSandboxAdapter()
        )
        result = adapter.dispatch(approval)
        settled_record, settled_ledger = SettlementService().settle(record, reserved, result)
        return self._persist(settled_record, settled_ledger)
