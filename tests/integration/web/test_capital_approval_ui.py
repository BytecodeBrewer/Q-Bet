from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.contrib.auth.models import User
from django.test import TestCase

from qbet.bank.balances import BankBalance
from qbet.bank.funding import (
    BankFundingProposal,
    FundingAccountRole,
    FundingDirection,
    FundingProposalState,
)
from qbet.data.models import DataSourceMetadata, FreshnessStatus, SourceTransport
from qbet.domain.ledger import PortfolioBalance
from qbet.ledger import PortfolioLedger
from qbet.storage.capital_movement import CapitalMovementRepository
from qbet.storage.capital_workflow import (
    CapitalActionMethod,
    CapitalFundingWorkflowRecord,
    CapitalFundingWorkflowRepository,
)
from qbet.storage.ledger import PortfolioLedgerRepository
from qbet.storage.models import NotificationInboxDeliveryRow

PROPOSAL_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
REQUIREMENT_ID = UUID("11111111-2222-3333-4444-555555555555")
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")
SOURCE = DataSourceMetadata(
    provider_id="bunq",
    source_id="capital-web-test",
    transport=SourceTransport.API,
)


class CapitalApprovalUiTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="test-password",
        )
        self.other = User.objects.create_user(
            username="other",
            email="other@example.com",
            password="test-password",
        )
        PortfolioLedgerRepository().save(
            PortfolioLedger(
                balance=PortfolioBalance(
                    mode="execution",
                    currency="EUR",
                    available=Decimal("100"),
                )
            )
        )

    def _publish_manual_attention(self) -> BankFundingProposal:
        now = datetime.now(UTC).replace(microsecond=0)
        proposal = BankFundingProposal(
            id=PROPOSAL_ID,
            requirement_id=REQUIREMENT_ID,
            direction=FundingDirection.FUNDING,
            source_role=FundingAccountRole.BANK_ACCOUNT,
            destination_role=FundingAccountRole.PORTFOLIO_LEDGER,
            source_location="bunq-***1234",
            destination_location="owner:execution",
            amount=Decimal("25"),
            currency="EUR",
            reason="capital_shortage",
            opportunity_id="opportunity-1",
            target_mode="execution",
            target_context="owner:execution",
            correlation_id=CORRELATION_ID,
            required_by=now + timedelta(minutes=30),
            engine="sports_capital",
            workflow_reference="workflow-1",
            attention_created_at=now,
            created_at=now,
            expires_at=now + timedelta(minutes=30),
            lifecycle_at=now,
        )
        balance = BankBalance(
            source=SOURCE,
            account_reference="bunq-***1234",
            currency="EUR",
            available_balance=Decimal("500"),
            current_balance=Decimal("500"),
            observed_at=now,
            freshness=FreshnessStatus.FRESH,
            correlation_id=CORRELATION_ID,
        )
        record = CapitalFundingWorkflowRecord(
            proposal=proposal,
            owner_id=self.user.get_username(),
            balance=balance,
            action_method=CapitalActionMethod.MANUAL,
            max_amount=Decimal("100"),
            max_balance_age_seconds=300,
        )
        published = CapitalFundingWorkflowRepository().publish_attention(
            record,
            requested_at=now + timedelta(seconds=1),
        )
        return published.proposal

    def test_funding_attention_precedes_decision_and_is_visible_in_inbox(self) -> None:
        proposal = self._publish_manual_attention()

        self.assertEqual(proposal.state, FundingProposalState.AWAITING_APPROVAL)
        self.assertTrue(
            NotificationInboxDeliveryRow.objects.filter(
                user_id="owner",
                task_id=PROPOSAL_ID,
                category="funding_attention",
            ).exists()
        )
        self.assertIsNone(CapitalMovementRepository().load_by_proposal(PROPOSAL_ID))

        self.client.force_login(self.user)
        inbox = self.client.get("/notifications/")
        approvals = self.client.get("/capital/approvals/")

        self.assertEqual(inbox.status_code, 200)
        self.assertContains(inbox, "Capital approval required")
        self.assertContains(inbox, "25")
        self.assertContains(inbox, "owner:execution")
        self.assertEqual(approvals.status_code, 200)
        self.assertContains(approvals, "bunq-***1234")
        self.assertContains(approvals, "owner:execution")
        self.assertContains(approvals, "capital_shortage")
        self.assertContains(approvals, "Approve")
        self.assertContains(approvals, "Reject")
        self.assertNotContains(approvals, "Mark transfer as performed")

    def test_other_user_cannot_view_or_decide_owner_capital_proposal(self) -> None:
        self._publish_manual_attention()
        self.client.force_login(self.other)

        listing = self.client.get("/capital/approvals/")
        denied = self.client.post(
            f"/capital/approvals/{PROPOSAL_ID}/decision/",
            {"decision": "approve"},
        )

        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, "capital_shortage")
        self.assertEqual(denied.status_code, 404)
        stored = CapitalFundingWorkflowRepository().load(PROPOSAL_ID)
        assert stored is not None
        self.assertEqual(stored.proposal.state, FundingProposalState.AWAITING_APPROVAL)
        self.assertIsNone(CapitalMovementRepository().load_by_proposal(PROPOSAL_ID))

    def test_rejection_exposes_no_transfer_instruction_or_movement(self) -> None:
        self._publish_manual_attention()
        self.client.force_login(self.user)

        response = self.client.post(
            f"/capital/approvals/{PROPOSAL_ID}/decision/",
            {"decision": "reject"},
            follow=True,
        )

        self.assertContains(response, "Rejected. No transfer instruction")
        self.assertNotContains(response, "Mark transfer as performed")
        stored = CapitalFundingWorkflowRepository().load(PROPOSAL_ID)
        assert stored is not None
        self.assertEqual(stored.proposal.state, FundingProposalState.REJECTED)
        self.assertIsNone(CapitalMovementRepository().load_by_proposal(PROPOSAL_ID))
        ledger = PortfolioLedgerRepository().load(mode="execution", currency="EUR")
        assert ledger is not None
        self.assertEqual(ledger.balance.available, Decimal("100"))

    def test_approval_then_manual_performed_creates_pending_without_ledger_credit(self) -> None:
        self._publish_manual_attention()
        self.client.force_login(self.user)

        approved = self.client.post(
            f"/capital/approvals/{PROPOSAL_ID}/decision/",
            {"decision": "approve"},
            follow=True,
        )

        self.assertContains(approved, "Manual instruction")
        self.assertContains(approved, "Mark transfer as performed")
        self.assertContains(approved, "bunq-***1234")
        self.assertContains(approved, "owner:execution")
        self.assertIsNone(CapitalMovementRepository().load_by_proposal(PROPOSAL_ID))
        before = PortfolioLedgerRepository().load(mode="execution", currency="EUR")
        assert before is not None
        self.assertEqual(before.balance.available, Decimal("100"))

        performed = self.client.post(
            f"/capital/approvals/{PROPOSAL_ID}/performed/",
            follow=True,
        )

        self.assertContains(performed, "Movement state: <strong>pending</strong>", html=True)
        self.assertNotContains(performed, "Mark transfer as performed")
        movement = CapitalMovementRepository().load_by_proposal(PROPOSAL_ID)
        assert movement is not None
        self.assertEqual(movement.state.value, "pending")
        self.assertFalse(movement.ledger_applied)
        after = PortfolioLedgerRepository().load(mode="execution", currency="EUR")
        assert after is not None
        self.assertEqual(after.balance.available, Decimal("100"))
        self.assertEqual(len(after.commands), 0)
