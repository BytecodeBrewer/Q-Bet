"""Validated settlement is the only dispatch-result capital writer."""

from qbet.domain.ledger import LedgerCommand, LedgerOperation
from qbet.execution.models import ExecutionRecord, Lifecycle, SandboxResult
from qbet.execution.sandbox import SandboxRequestHandler
from qbet.ledger import PortfolioLedger


def ledger_command(record: ExecutionRecord, operation: LedgerOperation, amount) -> LedgerCommand:
    work = record.proposal.work
    return LedgerCommand(
        id=f"{work.id}:{operation.value}",
        dispatch_id=str(work.id),
        correlation_id=str(work.correlation_id),
        currency=record.proposal.currency,
        operation=operation,
        amount=amount,
    )


def transition(record: ExecutionRecord, state: Lifecycle, **changes) -> ExecutionRecord:
    return ExecutionRecord.model_validate(
        {
            **record.model_dump(),
            "state": state,
            "transitions": (*record.transitions, state),
            **changes,
        }
    )


class SettlementService:
    def settle(
        self, record: ExecutionRecord, ledger: PortfolioLedger, result: SandboxResult
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        if record.state in {
            Lifecycle.SETTLED,
            Lifecycle.FAILED,
            Lifecycle.CANCELLED,
        }:
            if record.result == result:
                return record, ledger
            return record.model_copy(update={"error": "settlement_result_conflict"}), ledger
        if record.state is not Lifecycle.ACKNOWLEDGED:
            return record.model_copy(update={"error": "invalid_settlement_state"}), ledger
        reason = SandboxRequestHandler().validate_result(record.proposal, result)
        if reason:
            return record.model_copy(update={"error": reason}), ledger
        if ledger.balance.mode != record.proposal.work.mode.value:
            return record.model_copy(update={"error": "ledger_mode_mismatch"}), ledger
        operation = LedgerOperation.SETTLE if result.status == "success" else LedgerOperation.FAIL
        amount = result.payout if result.status == "success" else record.proposal.capital_required
        updated, decision = ledger.apply(ledger_command(record, operation, amount))
        if not decision.accepted:
            return record.model_copy(update={"error": decision.reason}), ledger
        state = {
            "success": Lifecycle.SETTLED,
            "failed": Lifecycle.FAILED,
            "cancelled": Lifecycle.CANCELLED,
        }[result.status]
        return transition(record, state, result=result, error=None), updated
