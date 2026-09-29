from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from django.test import TestCase

from qbet.bank.funding import (
    BankFundingProposal,
    FundingAccountRole,
    FundingApproval,
    FundingApprover,
    FundingDirection,
    FundingProposalState,
)
from qbet.bank.movement import (
    CapitalMovementObservation,
    CapitalMovementObservationStatus,
    CapitalMovementState,
)
from qbet.domain.ledger import PortfolioBalance
from qbet.ledger import PortfolioLedger
from qbet.storage.capital_movement import (
    CapitalMovementConflict,
    CapitalMovementRepository,
)
from qbet.storage.ledger import PortfolioLedgerRepository

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
PROPOSAL_ID = UUID("87654321-4321-8765-4321-876543218765")
CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def proposal(
    *,
    direction: FundingDirection = FundingDirection.FUNDING,
    amount: Decimal = Decimal("25"),
) -> BankFundingProposal:
    if direction is FundingDirection.FUNDING:
        source_role = FundingAccountRole.BANK_ACCOUNT
        destination_role = FundingAccountRole.PORTFOLIO_LEDGER
        source_location = "bunq-***1234"
        destination_location = "owner:execution"
    else:
        source_role = FundingAccountRole.PORTFOLIO_LEDGER
        destination_role = FundingAccountRole.BANK_ACCOUNT
        source_location = "owner:execution"
        destination_location = "bunq-***1234"

    return BankFundingProposal(
        id=PROPOSAL_ID,
        direction=direction,
        source_role=source_role,
        destination_role=destination_role,
        source_location=source_location,
        destination_location=destination_location,
        amount=amount,
        currency="EUR",
        reason="capital_rebalance",
        target_mode="execution",
        target_context="owner:execution",
        correlation_id=CORRELATION_ID,
        created_at=NOW - timedelta(minutes=2),
        expires_at=NOW + timedelta(minutes=30),
        state=FundingProposalState.APPROVED,
        lifecycle_at=NOW - timedelta(minutes=1),
        approval=FundingApproval(
            approver=FundingApprover(identity="owner", is_authenticated=True),
            approved_at=NOW - timedelta(minutes=1),
        ),
    )


def ledger(*, available: Decimal = Decimal("100")) -> PortfolioLedger:
    return PortfolioLedger(
        balance=PortfolioBalance(
            mode="execution",
            currency="EUR",
            available=available,
        )
    )


def confirmed(
    item: BankFundingProposal,
    *,
    amount: Decimal | None = None,
) -> CapitalMovementObservation:
    return CapitalMovementObservation(
        status=CapitalMovementObservationStatus.CONFIRMED,
        observed_at=NOW + timedelta(minutes=2),
        source="bank_transaction_feed",
        evidence_reference="transaction-***4321",
        amount=item.amount if amount is None else amount,
        currency=item.currency,
        source_location=item.source_location,
        destination_location=item.destination_location,
    )


class CapitalMovementRepositoryTests(TestCase):
    def setUp(self) -> None:
        PortfolioLedgerRepository().save(ledger())

    def test_manual_performed_is_pending_and_does_not_mutate_ledger(self) -> None:
        repository = CapitalMovementRepository()
        item = proposal()

        pending = repository.record_manual(item, performed_at=NOW)
        restored = CapitalMovementRepository().load_by_proposal(item.id)
        current = PortfolioLedgerRepository().load(mode="execution", currency="EUR")

        self.assertEqual(pending.state, CapitalMovementState.PENDING)
        self.assertFalse(pending.ledger_applied)
        self.assertEqual(restored, pending)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.balance.available, Decimal("100"))

    def test_confirmed_funding_reconciliation_credits_ledger_exactly_once(self) -> None:
        repository = CapitalMovementRepository()
        item = proposal()
        pending = repository.record_manual(item, performed_at=NOW)
        observation = confirmed(item)

        first = repository.reconcile(pending.id, observation)
        replay = CapitalMovementRepository().reconcile(pending.id, observation)
        current = PortfolioLedgerRepository().load(mode="execution", currency="EUR")

        self.assertEqual(first.state, CapitalMovementState.RECONCILED)
        self.assertTrue(first.ledger_applied)
        self.assertTrue(replay.duplicate)
        self.assertTrue(replay.ledger_applied)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.balance.available, Decimal("125"))
        self.assertEqual(len(current.commands), 1)

    def test_confirmed_withdrawal_reconciliation_debits_ledger_exactly_once(self) -> None:
        repository = CapitalMovementRepository()
        item = proposal(direction=FundingDirection.WITHDRAWAL)
        pending = repository.record_manual(item, performed_at=NOW)
        observation = confirmed(item)

        first = repository.reconcile(pending.id, observation)
        replay = CapitalMovementRepository().reconcile(pending.id, observation)
        current = PortfolioLedgerRepository().load(mode="execution", currency="EUR")

        self.assertEqual(first.state, CapitalMovementState.RECONCILED)
        self.assertTrue(first.ledger_applied)
        self.assertTrue(replay.duplicate)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.balance.available, Decimal("75"))
        self.assertEqual(len(current.commands), 1)

    def test_mismatch_is_visible_and_leaves_authoritative_ledger_unchanged(self) -> None:
        repository = CapitalMovementRepository()
        item = proposal()
        pending = repository.record_manual(item, performed_at=NOW)

        mismatch = repository.reconcile(
            pending.id,
            confirmed(item, amount=Decimal("24")),
        )
        current = PortfolioLedgerRepository().load(mode="execution", currency="EUR")

        self.assertEqual(mismatch.state, CapitalMovementState.MISMATCH)
        self.assertEqual(
            mismatch.reason_code,
            "capital_movement_reconciliation_mismatch",
        )
        self.assertFalse(mismatch.ledger_applied)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.balance.available, Decimal("100"))
        self.assertEqual(len(current.commands), 0)

    def test_failed_observation_is_terminal_without_ledger_mutation(self) -> None:
        repository = CapitalMovementRepository()
        item = proposal()
        pending = repository.record_manual(item, performed_at=NOW)

        failed = repository.reconcile(
            pending.id,
            CapitalMovementObservation(
                status=CapitalMovementObservationStatus.FAILED,
                observed_at=NOW + timedelta(minutes=1),
                source="bank_transaction_feed",
                reason_code="provider_reported_failure",
            ),
        )
        current = PortfolioLedgerRepository().load(mode="execution", currency="EUR")

        self.assertEqual(failed.state, CapitalMovementState.FAILED)
        self.assertFalse(failed.ledger_applied)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.balance.available, Decimal("100"))

    def test_adapter_acknowledgement_is_idempotent_across_repository_recreation(self) -> None:
        item = proposal()
        first = CapitalMovementRepository().record_adapter_acknowledgement(
            item,
            provider_reference="bunq-payment-0123456789ab",
            acknowledged_at=NOW,
        )
        replay = CapitalMovementRepository().record_adapter_acknowledgement(
            item,
            provider_reference="bunq-payment-0123456789ab",
            acknowledged_at=NOW + timedelta(seconds=5),
        )

        self.assertEqual(first.state, CapitalMovementState.PENDING)
        self.assertTrue(replay.duplicate)
        self.assertEqual(replay.performed_at, NOW)

    def test_conflicting_adapter_replay_fails_closed_without_ledger_mutation(self) -> None:
        item = proposal()
        CapitalMovementRepository().record_adapter_acknowledgement(
            item,
            provider_reference="bunq-payment-0123456789ab",
            acknowledged_at=NOW,
        )

        with self.assertRaisesRegex(
            CapitalMovementConflict,
            "capital_movement_identity_conflict",
        ):
            CapitalMovementRepository().record_adapter_acknowledgement(
                item,
                provider_reference="bunq-payment-feedface9876",
                acknowledged_at=NOW + timedelta(seconds=5),
            )

        current = PortfolioLedgerRepository().load(mode="execution", currency="EUR")
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.balance.available, Decimal("100"))
        self.assertEqual(len(current.commands), 0)

    def test_adapter_failure_is_persisted_without_ledger_mutation(self) -> None:
        item = proposal()

        failed = CapitalMovementRepository().record_adapter_failure(
            item,
            reason_code="sandbox_timeout",
            failed_at=NOW,
        )
        restored = CapitalMovementRepository().load(failed.id)
        current = PortfolioLedgerRepository().load(mode="execution", currency="EUR")

        self.assertEqual(failed.state, CapitalMovementState.FAILED)
        self.assertEqual(restored, failed)
        self.assertFalse(failed.ledger_applied)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.balance.available, Decimal("100"))
