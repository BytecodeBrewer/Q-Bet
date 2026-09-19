from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.test import TestCase

from qbet.bank.bunq import BunqSandboxPaymentResult
from qbet.bank.funding import (
    BankFundingProposal,
    FundingAccountRole,
    FundingApproval,
    FundingApprover,
    FundingDirection,
    FundingProposalState,
)
from qbet.domain.ledger import PortfolioBalance
from qbet.ledger import PortfolioLedger
from qbet.storage.funding import (
    SandboxFundingFeedbackConflict,
    SandboxFundingFeedbackRepository,
)
from qbet.storage.ledger import PortfolioLedgerRepository
from qbet.storage.models import SandboxFundingOutcomeRow
from qbet.storage.simulation_ledger import SimulationPortfolioLedgerRepository

NOW = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
PROPOSAL_ID = UUID("87654321-4321-8765-4321-876543218765")
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def proposal(**changes: object) -> BankFundingProposal:
    values: dict[str, object] = {
        "id": PROPOSAL_ID,
        "direction": FundingDirection.FUNDING,
        "source_role": FundingAccountRole.BANK_ACCOUNT,
        "destination_role": FundingAccountRole.PORTFOLIO_LEDGER,
        "amount": Decimal("0.01"),
        "currency": "EUR",
        "reason": "bunq_sandbox_simulation_funding",
        "target_mode": "simulation",
        "target_context": "owner:simulation",
        "correlation_id": CORRELATION_ID,
        "created_at": NOW - timedelta(minutes=2),
        "expires_at": NOW + timedelta(minutes=30),
        "state": FundingProposalState.APPROVED,
        "lifecycle_at": NOW,
        "approval": FundingApproval(
            approver=FundingApprover(identity="staff-1", is_authenticated=True),
            approved_at=NOW,
        ),
    }
    values.update(changes)
    return BankFundingProposal.model_validate(values)


def result(**changes: object) -> BunqSandboxPaymentResult:
    values: dict[str, object] = {
        "proposal_id": PROPOSAL_ID,
        "correlation_id": CORRELATION_ID,
        "test_reference": f"qbet-sandbox-{PROPOSAL_ID}",
        "sent": True,
        "provider_reference": "bunq-payment-deadbeef1234",
    }
    values.update(changes)
    return BunqSandboxPaymentResult.model_validate(values)


def initial_ledger(*, mode: str = "simulation") -> PortfolioLedger:
    return PortfolioLedger(
        balance=PortfolioBalance(
            mode=mode,
            currency="EUR",
            available=Decimal("10"),
        )
    )


class SandboxFundingFeedbackRepositoryTests(TestCase):
    def setUp(self) -> None:
        SimulationPortfolioLedgerRepository().load_or_create(initial_ledger())

    def test_success_is_persisted_and_credits_simulation_ledger_once_after_recreation(self) -> None:
        first = SandboxFundingFeedbackRepository().apply(proposal(), result())
        reloaded = SandboxFundingFeedbackRepository().load(PROPOSAL_ID)
        repeated = SandboxFundingFeedbackRepository().apply(proposal(), result())
        ledger = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")

        self.assertTrue(first.ledger_applied)
        self.assertEqual(reloaded, first)
        self.assertTrue(repeated.duplicate)
        self.assertEqual(SandboxFundingOutcomeRow.objects.count(), 1)
        self.assertIsNotNone(ledger)
        assert ledger is not None
        self.assertEqual(ledger.balance.available, Decimal("10.01"))

    def test_provider_failure_is_persisted_without_ledger_credit(self) -> None:
        failed = result(
            sent=False,
            provider_reference=None,
            reason_code="bunq_timeout",
        )

        feedback = SandboxFundingFeedbackRepository().apply(proposal(), failed)
        reloaded = SandboxFundingFeedbackRepository().load(PROPOSAL_ID)
        ledger = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")

        self.assertFalse(feedback.ledger_applied)
        self.assertEqual(reloaded, feedback)
        self.assertIsNotNone(ledger)
        assert ledger is not None
        self.assertEqual(ledger.balance.available, Decimal("10"))

    def test_conflicting_duplicate_fails_closed_without_second_credit(self) -> None:
        repository = SandboxFundingFeedbackRepository()
        repository.apply(proposal(), result())

        with self.assertRaisesRegex(
            SandboxFundingFeedbackConflict,
            "sandbox_funding_feedback_conflict",
        ):
            repository.apply(
                proposal(),
                result(provider_reference="bunq-payment-feedface9876"),
            )

        ledger = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        self.assertIsNotNone(ledger)
        assert ledger is not None
        self.assertEqual(ledger.balance.available, Decimal("10.01"))

    def test_execution_context_is_rejected_and_execution_ledger_stays_untouched(self) -> None:
        execution_ledger = initial_ledger(mode="execution").model_copy(
            update={
                "balance": PortfolioBalance(
                    mode="execution",
                    currency="EUR",
                    available=Decimal("500"),
                )
            }
        )
        PortfolioLedgerRepository().save(execution_ledger)
        execution = proposal(
            target_mode="execution",
            target_context="owner:execution",
        )

        with self.assertRaisesRegex(
            SandboxFundingFeedbackConflict,
            "sandbox_funding_feedback_invalid",
        ):
            SandboxFundingFeedbackRepository().apply(execution, result())

        simulation = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        execution_after = PortfolioLedgerRepository().load(mode="execution", currency="EUR")
        self.assertIsNotNone(simulation)
        self.assertIsNotNone(execution_after)
        assert simulation is not None and execution_after is not None
        self.assertEqual(simulation.balance.available, Decimal("10"))
        self.assertEqual(execution_after.balance.available, Decimal("500"))
        self.assertEqual(SandboxFundingOutcomeRow.objects.count(), 0)

    def test_raw_provider_reference_is_rejected_before_persistence(self) -> None:
        unsafe = result(provider_reference="bunq-payment-provider-12345")

        with self.assertRaisesRegex(
            SandboxFundingFeedbackConflict,
            "sandbox_funding_feedback_invalid",
        ):
            SandboxFundingFeedbackRepository().apply(proposal(), unsafe)

        ledger = PortfolioLedgerRepository().load(mode="simulation", currency="EUR")
        self.assertIsNotNone(ledger)
        assert ledger is not None
        self.assertEqual(ledger.balance.available, Decimal("10"))
        self.assertEqual(SandboxFundingOutcomeRow.objects.count(), 0)
