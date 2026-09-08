from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError, transaction
from django.test import TransactionTestCase

from qbet.calculations.qualifying_bet import QualifyingBetInput
from qbet.domain.ledger import PortfolioBalance
from qbet.engines import BonusEngineRequest
from qbet.execution.models import ExecutionProposal, ExecutionRecord, Lifecycle
from qbet.execution.sandbox import BonusSandboxAdapter, valuation
from qbet.execution.service import ExecutionService
from qbet.ledger import PortfolioLedger
from qbet.storage.ledger import (
    AuthoritativePersistenceError,
    AuthoritativeStateConflict,
    ExecutionStateRepository,
)
from qbet.workflow.models import WorkflowMode
from qbet.workflow.routing import RoutedWorkItem

NOW = datetime(2026, 9, 8, 12, tzinfo=UTC)


class _Interrupted(RuntimeError):
    pass


class _InterruptingWriter:
    def __init__(
        self,
        repository: ExecutionStateRepository,
        target: Lifecycle,
    ) -> None:
        self._repository = repository
        self._target = target
        self._interrupted = False

    def persist(
        self,
        record: ExecutionRecord,
        ledger: PortfolioLedger,
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        persisted = self._repository.persist(record, ledger)
        if record.state is self._target and not self._interrupted:
            self._interrupted = True
            raise _Interrupted(record.state.value)
        return persisted


class AuthoritativeExecutionRecoveryPostgresTests(TransactionTestCase):
    reset_sequences = True

    @staticmethod
    def _request(opportunity_id: str) -> BonusEngineRequest:
        return BonusEngineRequest(
            opportunity_id=opportunity_id,
            inputs=QualifyingBetInput(
                back_odds=Decimal("2"),
                lay_odds=Decimal("2.1"),
                back_stake=Decimal("10"),
                exchange_commission=Decimal("0.02"),
                stake_precision=Decimal("1"),
                max_lay_liability=Decimal("100"),
            ),
            currency="EUR",
            execution_offer_ids=("back", "lay"),
            generated_at=NOW,
        )

    def _record(self, opportunity_id: str) -> ExecutionRecord:
        request = self._request(opportunity_id)
        capital, payout = valuation(request)
        work = RoutedWorkItem(
            id=uuid4(),
            correlation_id=uuid4(),
            engine="bonus",
            mode=WorkflowMode.EXECUTION,
            opportunity_id=opportunity_id,
            capital_context="execution",
        )
        return ExecutionRecord(
            proposal=ExecutionProposal(
                work=work,
                request=request,
                expires_at=NOW + timedelta(minutes=5),
                currency="EUR",
                capital_required=capital,
                payout=payout,
            )
        )

    @staticmethod
    def _ledger() -> PortfolioLedger:
        return PortfolioLedger(
            balance=PortfolioBalance(
                mode="execution",
                currency="EUR",
                available=Decimal("1000"),
            )
        )

    def _assert_resume(
        self,
        target: Lifecycle,
        expected_position: str,
        opportunity_id: str,
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        initial_record = self._record(opportunity_id)
        initial_ledger = self._ledger()
        first_repository = ExecutionStateRepository()
        record, ledger = first_repository.load_or_create(initial_record, initial_ledger)

        with self.assertRaises(_Interrupted):
            ExecutionService(
                state_writer=_InterruptingWriter(first_repository, target)
            ).decide(
                record,
                ledger,
                actor="owner",
                owner="owner",
                approve=True,
                now=NOW,
            )

        second_repository = ExecutionStateRepository()
        reopened_record, reopened_ledger = second_repository.load_or_create(
            initial_record,
            initial_ledger,
        )
        self.assertEqual(reopened_record.state, target)
        self.assertEqual(
            reopened_ledger.positions[str(initial_record.proposal.work.id)].state,
            expected_position,
        )

        settled_record, settled_ledger = ExecutionService(
            state_writer=second_repository
        ).decide(
            reopened_record,
            reopened_ledger,
            actor="owner",
            owner="owner",
            approve=True,
            now=NOW,
        )
        self.assertEqual(settled_record.state, Lifecycle.SETTLED)
        self.assertEqual(
            settled_ledger.positions[str(initial_record.proposal.work.id)].state,
            "settled",
        )
        return settled_record, settled_ledger

    def test_resume_after_approved_checkpoint(self) -> None:
        self._assert_resume(
            Lifecycle.APPROVED,
            "reserved",
            "resume-approved",
        )

    def test_resume_after_dispatched_checkpoint(self) -> None:
        self._assert_resume(
            Lifecycle.DISPATCHED,
            "locked",
            "resume-dispatched",
        )

    def test_resume_after_acknowledged_checkpoint_and_duplicate_settlement(self) -> None:
        settled_record, settled_ledger = self._assert_resume(
            Lifecycle.ACKNOWLEDGED,
            "pending",
            "resume-acknowledged",
        )

        repository = ExecutionStateRepository()
        repeated_record, repeated_ledger = ExecutionService(
            state_writer=repository
        ).decide(
            settled_record,
            settled_ledger,
            actor="owner",
            owner="owner",
            approve=True,
            now=NOW,
        )

        self.assertEqual(repeated_record, settled_record)
        self.assertEqual(repeated_ledger, settled_ledger)
        self.assertEqual(len(repeated_ledger.commands), 4)

    def test_stale_competing_reservation_fails_before_adapter_side_effect(self) -> None:
        first_initial = self._record("concurrent-first")
        second_initial = self._record("concurrent-second")
        initial_ledger = self._ledger()

        first_repository = ExecutionStateRepository()
        first_record, first_ledger = first_repository.load_or_create(
            first_initial,
            initial_ledger,
        )
        second_repository = ExecutionStateRepository()
        second_record, second_ledger = second_repository.load_or_create(
            second_initial,
            initial_ledger,
        )

        with patch(
            "qbet.execution.service.BonusSandboxAdapter.dispatch",
            wraps=BonusSandboxAdapter().dispatch,
        ) as dispatch:
            ExecutionService(state_writer=first_repository).decide(
                first_record,
                first_ledger,
                actor="owner",
                owner="owner",
                approve=True,
                now=NOW,
            )
            self.assertEqual(dispatch.call_count, 1)

            with self.assertRaisesMessage(
                AuthoritativeStateConflict,
                "stale_portfolio_ledger",
            ):
                ExecutionService(state_writer=second_repository).decide(
                    second_record,
                    second_ledger,
                    actor="owner",
                    owner="owner",
                    approve=True,
                    now=NOW,
                )

            self.assertEqual(dispatch.call_count, 1)

        reopened_second, _ = second_repository.load_or_create(
            second_initial,
            initial_ledger,
        )
        self.assertEqual(reopened_second.state, Lifecycle.AWAITING_APPROVAL)

    def test_adapter_failure_persists_failed_state_and_releases_locked_capital(self) -> None:
        initial_record = self._record("adapter-failure")
        initial_ledger = self._ledger()
        repository = ExecutionStateRepository()
        record, ledger = repository.load_or_create(initial_record, initial_ledger)

        with patch(
            "qbet.execution.service.BonusSandboxAdapter.dispatch",
            side_effect=RuntimeError("sandbox failure"),
        ):
            failed_record, failed_ledger = ExecutionService(
                state_writer=repository
            ).decide(
                record,
                ledger,
                actor="owner",
                owner="owner",
                approve=True,
                now=NOW,
            )

        self.assertEqual(failed_record.state, Lifecycle.FAILED)
        self.assertEqual(failed_record.error, "sandbox_dispatch_failed")
        self.assertEqual(
            failed_ledger.positions[str(initial_record.proposal.work.id)].state,
            "failed",
        )
        self.assertEqual(failed_ledger.balance.available, Decimal("1000"))

        reopened_record, reopened_ledger = ExecutionStateRepository().load_or_create(
            initial_record,
            initial_ledger,
        )
        self.assertEqual(reopened_record, failed_record)
        self.assertEqual(reopened_ledger, failed_ledger)

    def test_transaction_exit_database_error_maps_to_stable_authoritative_error(self) -> None:
        initial_record = self._record("commit-boundary")
        initial_ledger = self._ledger()
        repository = ExecutionStateRepository()
        record, ledger = repository.load_or_create(initial_record, initial_ledger)
        original_exit = transaction.Atomic.__exit__

        def fail_after_exit(atomic, exc_type, exc_value, traceback):
            result = original_exit(atomic, exc_type, exc_value, traceback)
            raise DatabaseError("injected commit boundary failure")

        with patch.object(transaction.Atomic, "__exit__", new=fail_after_exit):
            with self.assertRaisesMessage(
                AuthoritativePersistenceError,
                "authoritative_execution_state_unavailable",
            ):
                repository.persist(record, ledger)
