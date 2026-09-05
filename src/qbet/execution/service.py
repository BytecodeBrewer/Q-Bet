"""Pure approval and dispatch transitions, committed atomically by storage."""

from datetime import datetime

from qbet.domain.ledger import LedgerOperation
from qbet.execution.models import ApprovedExecutionRequest, ExecutionRecord, Lifecycle
from qbet.execution.sandbox import (
    BonusSandboxAdapter, SandboxRequestHandler, SportsCapitalSandboxAdapter,
)
from qbet.ledger import PortfolioLedger
from qbet.settlement import SettlementService, ledger_command, transition


class ExecutionService:
    def decide(
        self, record: ExecutionRecord, ledger: PortfolioLedger, *,
        actor: str, owner: str, approve: bool, now: datetime,
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        if not actor or actor != owner:
            raise PermissionError("proposal_owner_required")
        if record.state is not Lifecycle.AWAITING_APPROVAL:
            return record, ledger
        if not approve:
            return transition(record, Lifecycle.REJECTED), ledger
        reason = SandboxRequestHandler().validate(record.proposal, now)
        if ledger.balance.mode != record.proposal.work.mode.value:
            reason = "ledger_mode_mismatch"
        if reason:
            return transition(record, Lifecycle.REJECTED, error=reason), ledger
        reserved, decision = ledger.apply(
            ledger_command(record, LedgerOperation.RESERVE, record.proposal.capital_required)
        )
        if not decision.accepted:
            return transition(record, Lifecycle.REJECTED, error=decision.reason), ledger
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
        return SettlementService().settle(record, reserved, result)
