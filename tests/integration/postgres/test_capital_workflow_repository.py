from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.test import TestCase

from qbet.bank.balances import BankBalance
from qbet.bank.funding import (
    BankFundingProposal,
    FundingAccountRole,
    FundingApprover,
    FundingDirection,
    FundingProposalState,
)
from qbet.data.models import DataSourceMetadata, FreshnessStatus, SourceTransport
from qbet.domain.ledger import PortfolioBalance
from qbet.ledger import PortfolioLedger
from qbet.storage.capital_workflow import (
    CapitalActionMethod,
    CapitalFundingWorkflowConflict,
    CapitalFundingWorkflowError,
    CapitalFundingWorkflowRecord,
    CapitalFundingWorkflowRepository,
)
from qbet.storage.ledger import PortfolioLedgerRepository
from qbet.storage.models import CapitalFundingProposalRow, NotificationInboxDeliveryRow

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
PROPOSAL_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
REQUIREMENT_ID = UUID("11111111-2222-3333-4444-555555555555")
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
SOURCE = DataSourceMetadata(
    provider_id="bunq",
    source_id="capital-workflow-repository-test",
    transport=SourceTransport.API,
)


def workflow_record(
    *,
    owner_id: str = "owner",
    amount: Decimal = Decimal("25"),
) -> CapitalFundingWorkflowRecord:
    created_at = NOW - timedelta(seconds=1)
    proposal = BankFundingProposal(
        id=PROPOSAL_ID,
        requirement_id=REQUIREMENT_ID,
        direction=FundingDirection.FUNDING,
        source_role=FundingAccountRole.BANK_ACCOUNT,
        destination_role=FundingAccountRole.PORTFOLIO_LEDGER,
        source_location="bunq-***1234",
        destination_location=f"{owner_id}:execution",
        amount=amount,
        currency="EUR",
        reason="capital_shortage",
        opportunity_id="opportunity-1",
        target_mode="execution",
        target_context=f"{owner_id}:execution",
        correlation_id=CORRELATION_ID,
        required_by=NOW + timedelta(minutes=30),
        engine="sports_capital",
        workflow_reference="workflow-1",
        attention_created_at=created_at,
        created_at=created_at,
        expires_at=NOW + timedelta(minutes=30),
        lifecycle_at=created_at,
    )
    balance = BankBalance(
        source=SOURCE,
        account_reference="bunq-***1234",
        currency="EUR",
        available_balance=Decimal("500"),
        current_balance=Decimal("500"),
        observed_at=created_at,
        freshness=FreshnessStatus.FRESH,
        correlation_id=CORRELATION_ID,
    )
    return CapitalFundingWorkflowRecord(
        proposal=proposal,
        owner_id=owner_id,
        balance=balance,
        action_method=CapitalActionMethod.MANUAL,
        max_amount=Decimal("100"),
        max_balance_age_seconds=300,
    )


class CapitalFundingWorkflowRepositoryTests(TestCase):
    def setUp(self) -> None:
        PortfolioLedgerRepository().save(
            PortfolioLedger(
                balance=PortfolioBalance(
                    mode="execution",
                    currency="EUR",
                    available=Decimal("100"),
                )
            )
        )

    def test_publish_attention_is_idempotent_and_owner_scoped(self) -> None:
        repository = CapitalFundingWorkflowRepository()
        record = workflow_record()

        first = repository.publish_attention(record, requested_at=NOW)
        replay = repository.publish_attention(record, requested_at=NOW)

        self.assertEqual(first, replay)
        self.assertEqual(first.proposal.state, FundingProposalState.AWAITING_APPROVAL)
        self.assertEqual(repository.list_for("owner"), (first,))
        self.assertEqual(repository.list_for("other"), ())
        self.assertEqual(
            NotificationInboxDeliveryRow.objects.filter(
                user_id="owner",
                task_id=PROPOSAL_ID,
                category="funding_attention",
            ).count(),
            1,
        )

    def test_conflicting_replay_fails_closed_without_replacing_stored_identity(self) -> None:
        repository = CapitalFundingWorkflowRepository()
        first = repository.publish_attention(workflow_record(), requested_at=NOW)

        with self.assertRaisesMessage(
            CapitalFundingWorkflowConflict,
            "capital_proposal_identity_conflict",
        ):
            repository.publish_attention(
                workflow_record(amount=Decimal("30")),
                requested_at=NOW,
            )

        self.assertEqual(repository.load(PROPOSAL_ID), first)

    def test_corrupted_durable_metadata_fails_closed(self) -> None:
        repository = CapitalFundingWorkflowRepository()
        repository.publish_attention(workflow_record(), requested_at=NOW)
        CapitalFundingProposalRow.objects.filter(proposal_id=PROPOSAL_ID).update(
            owner_id="other"
        )

        with self.assertRaisesMessage(
            CapitalFundingWorkflowError,
            "capital_workflow_metadata_mismatch",
        ):
            repository.load(PROPOSAL_ID)

    def test_unauthenticated_approver_cannot_cross_the_repository_boundary(self) -> None:
        repository = CapitalFundingWorkflowRepository()
        repository.publish_attention(workflow_record(), requested_at=NOW)

        with self.assertRaisesRegex(ValueError, "approver_not_authenticated"):
            repository.decide(
                PROPOSAL_ID,
                approver=FundingApprover(
                    identity="owner",
                    is_authenticated=False,
                ),
                approve=True,
                decided_at=NOW + timedelta(seconds=1),
            )

        stored = repository.load(PROPOSAL_ID)
        assert stored is not None
        self.assertEqual(stored.proposal.state, FundingProposalState.AWAITING_APPROVAL)

    def test_missing_target_ledger_rejects_approval_without_state_change(self) -> None:
        repository = CapitalFundingWorkflowRepository()
        repository.publish_attention(workflow_record(), requested_at=NOW)
        from qbet.storage.models import PortfolioLedgerRow

        PortfolioLedgerRow.objects.all().delete()

        with self.assertRaisesMessage(
            CapitalFundingWorkflowError,
            "capital_ledger_missing",
        ):
            repository.decide(
                PROPOSAL_ID,
                approver=FundingApprover(identity="owner", is_authenticated=True),
                approve=True,
                decided_at=NOW + timedelta(seconds=1),
            )

        stored = repository.load(PROPOSAL_ID)
        assert stored is not None
        self.assertEqual(stored.proposal.state, FundingProposalState.AWAITING_APPROVAL)
