"""Compose completed Simulation work with approved bunq sandbox funding feedback."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from qbet.bank.balances import BankBalance
from qbet.bank.bunq import BunqTransport
from qbet.bank.funding import (
    BankFundingProposal,
    BankFundingProposalService,
    FundingAccountRole,
    FundingApprover,
    FundingDirection,
)
from qbet.storage.funding import (
    BunqSandboxSimulationFundingService,
    SandboxFundingFeedbackRecord,
)
from qbet.storage.ledger import PortfolioLedgerRepository
from qbet.workflow.models import WorkflowMode
from qbet.workflow.queue import QueuedWorkItem, WorkState


class SimulationSandboxFundingCoordinator:
    """Create and execute one sandbox funding proposal from completed Simulation work."""

    def __init__(
        self,
        *,
        transport: BunqTransport,
        recipient_email: str | None,
        max_amount: Decimal,
        max_balance_age: timedelta,
        ledger_repository: PortfolioLedgerRepository | None = None,
        funding_service: BunqSandboxSimulationFundingService | None = None,
    ) -> None:
        self._policy = BankFundingProposalService(
            max_amount=max_amount,
            max_balance_age=max_balance_age,
        )
        self._ledger_repository = ledger_repository or PortfolioLedgerRepository()
        self._funding_service = funding_service or BunqSandboxSimulationFundingService(
            transport=transport,
            recipient_email=recipient_email,
        )

    def execute_for_completed_work(
        self,
        queued: QueuedWorkItem,
        *,
        amount: Decimal,
        balance: BankBalance,
        approver: FundingApprover,
        requested_at: datetime,
        approved_at: datetime,
        expires_at: datetime,
    ) -> SandboxFundingFeedbackRecord:
        work = queued.work
        if work.mode is not WorkflowMode.SIMULATION or queued.state is not WorkState.COMPLETED:
            raise ValueError("simulation_funding_work_not_completed")
        if balance.correlation_id != work.correlation_id:
            raise ValueError("simulation_funding_balance_correlation_mismatch")

        ledger = self._ledger_repository.load(mode="simulation", currency=balance.currency)
        if ledger is None:
            raise ValueError("simulation_funding_ledger_missing")

        created_at = queued.history[-1].recorded_at
        proposal = BankFundingProposal(
            id=uuid5(NAMESPACE_URL, f"qbet:simulation-funding:{work.id}"),
            direction=FundingDirection.FUNDING,
            source_role=FundingAccountRole.BANK_ACCOUNT,
            destination_role=FundingAccountRole.PORTFOLIO_LEDGER,
            amount=amount,
            currency=balance.currency,
            reason="completed_simulation_sandbox_funding",
            target_mode="simulation",
            target_context=work.capital_context,
            correlation_id=work.correlation_id,
            created_at=created_at,
            expires_at=expires_at,
            lifecycle_at=created_at,
        )
        awaiting = self._policy.request_approval(proposal, requested_at=requested_at)
        if not awaiting.accepted:
            raise ValueError(awaiting.reason_code or "simulation_funding_approval_request_rejected")

        approved = self._policy.approve(
            awaiting.proposal,
            approver=approver,
            approved_at=approved_at,
            balance=balance,
            ledger=ledger,
            capital_context=work.capital_context,
        )
        if not approved.accepted:
            raise ValueError(approved.reason_code or "simulation_funding_approval_rejected")

        return self._funding_service.execute(approved.proposal)
