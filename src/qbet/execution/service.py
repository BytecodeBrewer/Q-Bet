"""Pure approval and dispatch transitions, committed atomically by storage."""

from datetime import datetime
from typing import Protocol

from qbet.data.results import ResultCollectionOutcome
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


class ExecutionStateWriter(Protocol):
    def persist(
        self, record: ExecutionRecord, ledger: PortfolioLedger
    ) -> tuple[ExecutionRecord, PortfolioLedger]: ...


_TERMINAL_STATES = {
    Lifecycle.REJECTED,
    Lifecycle.FAILED,
    Lifecycle.SETTLED,
    Lifecycle.CANCELLED,
}


class ExecutionService:
    def __init__(
        self,
        *,
        state_writer: ExecutionStateWriter | None = None,
        ledger_writer: LedgerWriter | None = None,
        execution_writer: ExecutionWriter | None = None,
    ) -> None:
        self._state_writer = state_writer
        self._ledger_writer = ledger_writer
        self._execution_writer = execution_writer

    def _persist(
        self, record: ExecutionRecord, ledger: PortfolioLedger
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        if self._state_writer is not None:
            return self._state_writer.persist(record, ledger)
        if self._ledger_writer is not None:
            ledger = self._ledger_writer.save(ledger)
        if self._execution_writer is not None:
            record = self._execution_writer.save(record)
        return record, ledger

    @staticmethod
    def _approval(record: ExecutionRecord) -> ApprovedExecutionRequest:
        if record.approval is None:
            raise ValueError("approved_execution_missing_approval")
        return record.approval

    @staticmethod
    def _apply(
        record: ExecutionRecord,
        ledger: PortfolioLedger,
        operation: LedgerOperation,
        amount,
    ) -> PortfolioLedger:
        updated, decision = ledger.apply(ledger_command(record, operation, amount))
        if not decision.accepted:
            raise ValueError(decision.reason or "ledger_transition_rejected")
        return updated

    @staticmethod
    def _awaits_post_event_result(
        record: ExecutionRecord,
        collected_result: ResultCollectionOutcome | None,
    ) -> bool:
        proposal = record.proposal
        return (
            collected_result is None
            and proposal.result_source is not None
            and proposal.result_provider_target is not None
        )

    def record_decision(
        self,
        record: ExecutionRecord,
        ledger: PortfolioLedger,
        *,
        actor: str,
        owner: str,
        approve: bool,
        now: datetime,
        notification_email: str = "",
        notification_display_name: str = "",
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        """Persist a human decision without reserving capital or dispatching an adapter."""

        if not actor or actor != owner:
            raise PermissionError("proposal_owner_required")

        if record.state in _TERMINAL_STATES:
            return self._persist(record, ledger)
        if record.state is Lifecycle.APPROVED:
            return self._persist(record, ledger)
        if record.state is not Lifecycle.AWAITING_APPROVAL:
            raise ValueError("execution_not_awaiting_approval")

        if not approve:
            return self._persist(
                transition(record, Lifecycle.REJECTED),
                ledger,
            )

        reason = SandboxRequestHandler().validate(record.proposal, now)
        if ledger.balance.mode != record.proposal.work.mode.value:
            reason = "ledger_mode_mismatch"
        if reason:
            return self._persist(
                transition(record, Lifecycle.REJECTED, error=reason),
                ledger,
            )

        approval = ApprovedExecutionRequest(
            proposal=record.proposal,
            approved_by=actor,
            approved_at=now,
            notification_email=notification_email,
            notification_display_name=notification_display_name,
        )
        return self._persist(
            transition(record, Lifecycle.APPROVED, approval=approval),
            ledger,
        )

    def cancel_before_dispatch(
        self,
        record: ExecutionRecord,
        ledger: PortfolioLedger,
        *,
        reason: str,
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        """Cancel prepared work while no adapter-side effect has occurred."""

        if record.state in _TERMINAL_STATES:
            return self._persist(record, ledger)
        if record.state not in {Lifecycle.AWAITING_APPROVAL, Lifecycle.APPROVED}:
            raise ValueError("execution_already_dispatched")
        return self._persist(
            transition(record, Lifecycle.CANCELLED, error=reason),
            ledger,
        )

    def execute_approved(
        self,
        record: ExecutionRecord,
        ledger: PortfolioLedger,
        *,
        now: datetime,
        collected_result: ResultCollectionOutcome | None = None,
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        """Resume an explicitly approved record after the workflow revalidation gate."""

        if record.state in _TERMINAL_STATES:
            return self._persist(record, ledger)
        if record.state is Lifecycle.AWAITING_APPROVAL:
            return self._persist(record, ledger)

        if record.state is Lifecycle.APPROVED:
            if collected_result is not None:
                SettlementService().validates_collected_result(record, collected_result)
            reason = SandboxRequestHandler().validate(record.proposal, now)
            if ledger.balance.mode != record.proposal.work.mode.value:
                reason = "ledger_mode_mismatch"
            if reason:
                return self._persist(
                    transition(record, Lifecycle.REJECTED, error=reason),
                    ledger,
                )

            reserved = self._apply(
                record,
                ledger,
                LedgerOperation.RESERVE,
                record.proposal.capital_required,
            )
            record, ledger = self._persist(record, reserved)
            locked = self._apply(
                record,
                ledger,
                LedgerOperation.LOCK,
                record.proposal.capital_required,
            )
            record, ledger = self._persist(
                transition(record, Lifecycle.DISPATCHED),
                locked,
            )

        if record.state is Lifecycle.DISPATCHED:
            approval = self._approval(record)
            adapter = (
                BonusSandboxAdapter()
                if record.proposal.work.engine == "bonus"
                else SportsCapitalSandboxAdapter()
            )
            try:
                result = adapter.dispatch(approval)
            except Exception:
                failed_ledger = self._apply(
                    record,
                    ledger,
                    LedgerOperation.FAIL,
                    record.proposal.capital_required,
                )
                return self._persist(
                    transition(
                        record,
                        Lifecycle.FAILED,
                        error="sandbox_dispatch_failed",
                    ),
                    failed_ledger,
                )

            pending = self._apply(
                record,
                ledger,
                LedgerOperation.PENDING,
                record.proposal.capital_required,
            )
            record, ledger = self._persist(
                transition(
                    record,
                    Lifecycle.ACKNOWLEDGED,
                    result=result,
                ),
                pending,
            )

        if record.state is Lifecycle.ACKNOWLEDGED:
            if record.result is None:
                raise ValueError("acknowledged_execution_missing_result")
            if self._awaits_post_event_result(record, collected_result):
                return self._persist(record, ledger)
            settled_record, settled_ledger = SettlementService().settle(
                record,
                ledger,
                record.result,
                collected_result=collected_result,
            )
            return self._persist(settled_record, settled_ledger)

        return self._persist(record, ledger)

    def decide(
        self,
        record: ExecutionRecord,
        ledger: PortfolioLedger,
        *,
        actor: str,
        owner: str,
        approve: bool,
        now: datetime,
        collected_result: ResultCollectionOutcome | None = None,
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        """Backward-compatible one-call deterministic decision/dispatch helper."""

        if not actor or actor != owner:
            raise PermissionError("proposal_owner_required")

        if record.state in _TERMINAL_STATES:
            return self._persist(record, ledger)

        if record.state is Lifecycle.AWAITING_APPROVAL:
            if not approve:
                return self._persist(
                    transition(record, Lifecycle.REJECTED),
                    ledger,
                )

            reason = SandboxRequestHandler().validate(record.proposal, now)
            if ledger.balance.mode != record.proposal.work.mode.value:
                reason = "ledger_mode_mismatch"
            if reason:
                return self._persist(
                    transition(record, Lifecycle.REJECTED, error=reason),
                    ledger,
                )
            if collected_result is not None:
                SettlementService().validates_collected_result(record, collected_result)

            reserved, decision = ledger.apply(
                ledger_command(
                    record,
                    LedgerOperation.RESERVE,
                    record.proposal.capital_required,
                )
            )
            if not decision.accepted:
                return self._persist(
                    transition(record, Lifecycle.REJECTED, error=decision.reason),
                    ledger,
                )

            approval = ApprovedExecutionRequest(
                proposal=record.proposal,
                approved_by=actor,
                approved_at=now,
            )
            record, ledger = self._persist(
                transition(record, Lifecycle.APPROVED, approval=approval),
                reserved,
            )

        if record.state is Lifecycle.APPROVED:
            if collected_result is not None:
                SettlementService().validates_collected_result(record, collected_result)
            locked = self._apply(
                record,
                ledger,
                LedgerOperation.LOCK,
                record.proposal.capital_required,
            )
            record, ledger = self._persist(
                transition(record, Lifecycle.DISPATCHED),
                locked,
            )

        if record.state is Lifecycle.DISPATCHED:
            approval = self._approval(record)
            adapter = (
                BonusSandboxAdapter()
                if record.proposal.work.engine == "bonus"
                else SportsCapitalSandboxAdapter()
            )
            try:
                result = adapter.dispatch(approval)
            except Exception:
                failed_ledger = self._apply(
                    record,
                    ledger,
                    LedgerOperation.FAIL,
                    record.proposal.capital_required,
                )
                return self._persist(
                    transition(
                        record,
                        Lifecycle.FAILED,
                        error="sandbox_dispatch_failed",
                    ),
                    failed_ledger,
                )

            pending = self._apply(
                record,
                ledger,
                LedgerOperation.PENDING,
                record.proposal.capital_required,
            )
            record, ledger = self._persist(
                transition(
                    record,
                    Lifecycle.ACKNOWLEDGED,
                    result=result,
                ),
                pending,
            )

        if record.state is Lifecycle.ACKNOWLEDGED:
            if record.result is None:
                raise ValueError("acknowledged_execution_missing_result")
            if self._awaits_post_event_result(record, collected_result):
                return self._persist(record, ledger)
            settled_record, settled_ledger = SettlementService().settle(
                record,
                ledger,
                record.result,
                collected_result=collected_result,
            )
            return self._persist(settled_record, settled_ledger)

        return self._persist(record, ledger)
