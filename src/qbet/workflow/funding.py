"""Compose completed Simulation work with approval-gated bunq sandbox funding."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime

from qbet.bank.balances import BankBalance
from qbet.bank.bunq import BunqTransport
from qbet.bank.funding import (
    FundingAccountRole,
    FundingDirection,
    FundingProposalState,
)
from qbet.bank.movement import (
    CapitalRequirement,
    CapitalRequirementDecisionKind,
    CapitalRequirementService,
)
from qbet.storage.capital_workflow import (
    CapitalActionMethod,
    CapitalFundingWorkflowConflict,
    CapitalFundingWorkflowRecord,
    CapitalFundingWorkflowRepository,
)
from qbet.storage.funding import (
    BunqSandboxSimulationFundingService,
    SandboxFundingFeedbackRecord,
)
from qbet.storage.ledger import PortfolioLedgerRepository
from qbet.workflow.models import WorkflowMode
from qbet.workflow.queue import QueuedWorkItem, WorkState


class SimulationSandboxFundingCoordinator:
    """Prepare attention first; execute sandbox funding only after separate approval."""

    def __init__(
        self,
        *,
        transport: BunqTransport,
        recipient_email: str | None,
        max_amount: Decimal,
        max_balance_age: timedelta,
        ledger_repository: PortfolioLedgerRepository | None = None,
        workflow_repository: CapitalFundingWorkflowRepository | None = None,
        funding_service: BunqSandboxSimulationFundingService | None = None,
    ) -> None:
        self._max_amount = max_amount
        self._max_balance_age = max_balance_age
        self._ledger_repository = ledger_repository or PortfolioLedgerRepository()
        self._workflow_repository = workflow_repository or CapitalFundingWorkflowRepository(
            ledger_repository=self._ledger_repository
        )
        self._requirement_service = CapitalRequirementService()
        self._funding_service = funding_service or BunqSandboxSimulationFundingService(
            transport=transport,
            recipient_email=recipient_email,
        )

    def prepare_for_completed_work(
        self,
        queued: QueuedWorkItem,
        *,
        amount: Decimal,
        balance: BankBalance,
        requested_at: AwareDatetime,
        expires_at: AwareDatetime,
    ) -> CapitalFundingWorkflowRecord:
        """Publish funding attention and stop at Awaiting Approval."""

        work = queued.work
        if work.mode is not WorkflowMode.SIMULATION or queued.state is not WorkState.COMPLETED:
            raise ValueError("simulation_funding_work_not_completed")
        if work.owner is None:
            raise ValueError("simulation_funding_owner_missing")
        if balance.correlation_id != work.correlation_id:
            raise ValueError("simulation_funding_balance_correlation_mismatch")

        ledger = self._ledger_repository.load(mode="simulation", currency=balance.currency)
        if ledger is None:
            raise ValueError("simulation_funding_ledger_missing")

        created_at = queued.history[-1].recorded_at
        requirement = CapitalRequirement(
            id=uuid5(NAMESPACE_URL, f"qbet:simulation-funding-requirement:{work.id}"),
            direction=FundingDirection.FUNDING,
            source_role=FundingAccountRole.BANK_ACCOUNT,
            destination_role=FundingAccountRole.PORTFOLIO_LEDGER,
            source_location=balance.account_reference,
            destination_location=work.capital_context,
            amount=amount,
            currency=balance.currency,
            reason="completed_simulation_sandbox_funding",
            opportunity_id=work.opportunity_id,
            correlation_id=work.correlation_id,
            required_by=expires_at,
            target_mode="simulation",
            target_context=work.capital_context,
            engine=work.engine,
            workflow_reference=str(work.id),
        )
        decision = self._requirement_service.propose(
            requirement,
            created_at=created_at,
            expires_at=expires_at,
        )
        if decision.kind is not CapitalRequirementDecisionKind.PROPOSAL:
            raise ValueError(decision.reason_code or "simulation_funding_requirement_unavailable")
        proposal = decision.proposal
        assert proposal is not None

        record = CapitalFundingWorkflowRecord(
            proposal=proposal,
            owner_id=work.owner,
            balance=balance,
            action_method=CapitalActionMethod.SANDBOX_ADAPTER,
            max_amount=self._max_amount,
            max_balance_age_seconds=max(0, int(self._max_balance_age.total_seconds())),
        )
        return self._workflow_repository.publish_attention(
            record,
            requested_at=requested_at,
        )

    def execute_approved(
        self,
        proposal_id: UUID,
        *,
        actor: str,
    ) -> SandboxFundingFeedbackRecord:
        """Execute the supported sandbox adapter only after a separate persisted approval."""

        record = self._workflow_repository.load(proposal_id)
        if record is None:
            raise KeyError(proposal_id)
        if record.owner_id != actor:
            raise PermissionError("capital_proposal_owner_mismatch")
        if record.action_method is not CapitalActionMethod.SANDBOX_ADAPTER:
            raise CapitalFundingWorkflowConflict("capital_action_not_sandbox_adapter")
        if record.proposal.state is not FundingProposalState.APPROVED:
            raise CapitalFundingWorkflowConflict("capital_proposal_not_approved")
        return self._funding_service.execute(record.proposal)
