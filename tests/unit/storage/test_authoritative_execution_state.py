from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError
from django.test import TestCase

from qbet.calculations.qualifying_bet import QualifyingBetInput
from qbet.domain.ledger import PortfolioBalance
from qbet.engines import BonusEngineRequest
from qbet.execution.models import ExecutionProposal, ExecutionRecord, Lifecycle
from qbet.execution.sandbox import valuation
from qbet.execution.service import ExecutionService
from qbet.ledger import PortfolioLedger
from qbet.settlement import transition
from qbet.storage.ledger import (
    AuthoritativePersistenceError,
    AuthoritativeStateConflict,
    ExecutionStateRepository,
    ModeWorkQueueRepository,
    PortfolioLedgerRepository,
)
from qbet.storage.models import ExecutionRecordRow, PortfolioLedgerRow
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.models import WorkflowMode
from qbet.workflow.queue import WorkState
from qbet.workflow.routing import EngineModes, RoutedWorkItem, RoutingConfiguration

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)


class AuthoritativeExecutionStateTests(TestCase):
    @staticmethod
    def _request(opportunity_id: str = "authoritative-state") -> BonusEngineRequest:
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

    def _record(self, opportunity_id: str = "authoritative-state") -> ExecutionRecord:
        request = self._request(opportunity_id)
        capital, payout = valuation(request)
        work = RoutedWorkItem(
            id=uuid4(),
            correlation_id=uuid4(),
            engine="bonus",
            mode=WorkflowMode.EXECUTION,
            opportunity_id=request.opportunity_id,
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
    def _ledger(mode: str = "execution") -> PortfolioLedger:
        return PortfolioLedger(
            balance=PortfolioBalance(
                mode=mode,
                currency="EUR",
                available=Decimal("1000"),
            )
        )

    @staticmethod
    def _settle(
        record: ExecutionRecord, ledger: PortfolioLedger
    ) -> tuple[ExecutionRecord, PortfolioLedger]:
        return ExecutionService().decide(
            record,
            ledger,
            actor="owner",
            owner="owner",
            approve=True,
            now=NOW,
        )

    def test_reopens_persisted_pair_and_rejects_conflicting_record(self) -> None:
        repository = ExecutionStateRepository()
        record = self._record()
        repository.load_or_create(record, self._ledger())

        reopened_record, reopened_ledger = repository.load_or_create(record, self._ledger())
        self.assertEqual(reopened_record, record)
        self.assertEqual(reopened_ledger, self._ledger())

        conflicting = record.model_copy(
            update={
                "proposal": record.proposal.model_copy(
                    update={
                        "work": record.proposal.work.model_copy(
                            update={"opportunity_id": "other"}
                        )
                    }
                )
            }
        )
        with self.assertRaisesMessage(
            AuthoritativeStateConflict, "execution_record_conflict"
        ):
            repository.load_or_create(conflicting, self._ledger())

    def test_settled_pair_survives_repository_recreation_and_duplicate_persist(self) -> None:
        initial_record = self._record()
        initial_ledger = self._ledger()
        first_repository = ExecutionStateRepository()
        record, ledger = first_repository.load_or_create(initial_record, initial_ledger)
        settled_record, settled_ledger = self._settle(record, ledger)
        first_repository.persist(settled_record, settled_ledger)

        second_repository = ExecutionStateRepository()
        reopened_record, reopened_ledger = second_repository.load_or_create(
            initial_record, initial_ledger
        )
        repeated_record, repeated_ledger = second_repository.persist(
            reopened_record, reopened_ledger
        )

        self.assertEqual(reopened_record, settled_record)
        self.assertEqual(reopened_ledger, settled_ledger)
        self.assertEqual(repeated_record, settled_record)
        self.assertEqual(repeated_ledger, settled_ledger)
        self.assertEqual(reopened_record.state, Lifecycle.SETTLED)
        self.assertIsNotNone(reopened_record.approval)
        self.assertIsNotNone(reopened_record.result)
        self.assertEqual(
            reopened_record.proposal.work.correlation_id,
            initial_record.proposal.work.correlation_id,
        )
        self.assertEqual(len(reopened_ledger.commands), 4)

    def test_stale_record_and_ledger_snapshots_cannot_overwrite_newer_state(self) -> None:
        repository = ExecutionStateRepository()
        initial_record = self._record()
        initial_ledger = self._ledger()
        record, ledger = repository.load_or_create(initial_record, initial_ledger)
        settled_record, settled_ledger = self._settle(record, ledger)
        repository.persist(settled_record, settled_ledger)

        with self.assertRaisesMessage(
            AuthoritativeStateConflict, "stale_execution_record"
        ):
            repository.persist(initial_record, settled_ledger)

        with self.assertRaisesMessage(
            AuthoritativeStateConflict, "stale_portfolio_ledger"
        ):
            repository.persist(settled_record, initial_ledger)

        reopened_record, reopened_ledger = repository.load_or_create(
            initial_record, initial_ledger
        )
        self.assertEqual(reopened_record, settled_record)
        self.assertEqual(reopened_ledger, settled_ledger)

    def test_conflicting_same_depth_record_is_rejected(self) -> None:
        repository = ExecutionStateRepository()
        record = self._record()
        ledger = self._ledger()
        repository.load_or_create(record, ledger)
        failed_record = transition(record, Lifecycle.FAILED, error="sandbox_failure")
        repository.persist(failed_record, ledger)

        competing = transition(record, Lifecycle.CANCELLED, error="cancelled_elsewhere")
        with self.assertRaisesMessage(
            AuthoritativeStateConflict, "stale_execution_record"
        ):
            repository.persist(competing, ledger)

    def test_failure_transition_is_restored_after_repository_recreation(self) -> None:
        initial_record = self._record()
        ledger = self._ledger()
        repository = ExecutionStateRepository()
        repository.load_or_create(initial_record, ledger)
        failed_record = transition(initial_record, Lifecycle.FAILED, error="sandbox_failure")
        repository.persist(failed_record, ledger)

        reopened_record, reopened_ledger = ExecutionStateRepository().load_or_create(
            initial_record, ledger
        )

        self.assertEqual(reopened_record.state, Lifecycle.FAILED)
        self.assertEqual(reopened_record.error, "sandbox_failure")
        self.assertEqual(reopened_record.transitions[-1], Lifecycle.FAILED)
        self.assertEqual(reopened_ledger, ledger)

    def test_second_write_failure_rolls_back_ledger_and_record_together(self) -> None:
        repository = ExecutionStateRepository()
        initial_record = self._record()
        initial_ledger = self._ledger()
        record, ledger = repository.load_or_create(initial_record, initial_ledger)
        settled_record, settled_ledger = self._settle(record, ledger)

        with patch.object(
            ExecutionRecordRow,
            "save",
            side_effect=DatabaseError("injected record write failure"),
        ):
            with self.assertRaisesMessage(
                AuthoritativePersistenceError,
                "authoritative_execution_state_unavailable",
            ):
                repository.persist(settled_record, settled_ledger)

        reopened_record, reopened_ledger = repository.load_or_create(
            initial_record, initial_ledger
        )
        self.assertEqual(reopened_record, initial_record)
        self.assertEqual(reopened_ledger, initial_ledger)

    def test_database_failure_maps_to_stable_fail_closed_error(self) -> None:
        repository = ExecutionStateRepository()
        record = self._record()
        ledger = self._ledger()
        repository.load_or_create(record, ledger)

        with patch.object(
            PortfolioLedgerRow.objects,
            "select_for_update",
            side_effect=DatabaseError("database unavailable"),
        ):
            with self.assertRaisesMessage(
                AuthoritativePersistenceError,
                "authoritative_execution_state_unavailable",
            ):
                repository.persist(record, ledger)

    def test_execution_updates_do_not_mutate_simulation_ledger(self) -> None:
        simulation_ledger = self._ledger(mode="simulation")
        PortfolioLedgerRepository().save(simulation_ledger)

        repository = ExecutionStateRepository()
        record = self._record()
        execution_ledger = self._ledger()
        restored_record, restored_ledger = repository.load_or_create(
            record, execution_ledger
        )
        settled_record, settled_ledger = self._settle(restored_record, restored_ledger)
        repository.persist(settled_record, settled_ledger)

        self.assertEqual(
            PortfolioLedgerRepository().load(mode="simulation", currency="EUR"),
            simulation_ledger,
        )
        self.assertEqual(
            PortfolioLedgerRepository().load(mode="execution", currency="EUR"),
            settled_ledger,
        )
        self.assertEqual(PortfolioLedgerRow.objects.count(), 2)

    def test_dispatch_marks_claimed_work_failed_when_authoritative_state_is_unavailable(
        self,
    ) -> None:
        request = self._request("dispatch-persistence-failure")
        coordinator = ModeDispatchCoordinator(
            RoutingConfiguration(bonus=EngineModes(execution=True))
        )
        (scheduled,) = coordinator.schedule(
            request,
            owner="owner",
            correlation_id=uuid4(),
            scheduled_for=NOW,
            expires_at=NOW + timedelta(minutes=5),
        )

        with patch(
            "qbet.workflow.dispatch.ExecutionStateRepository.load_or_create",
            side_effect=AuthoritativePersistenceError(
                "authoritative_execution_state_unavailable"
            ),
        ):
            (result,) = coordinator.dispatch_due(now=NOW, owner="owner")

        self.assertEqual(result.state, WorkState.FAILED)
        self.assertEqual(
            result.history[-1].reason,
            "authoritative_execution_state_unavailable",
        )
        persisted = ModeWorkQueueRepository().load(scheduled.work.id)
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted.state, WorkState.FAILED)
        self.assertEqual(
            persisted.history[-1].reason,
            "authoritative_execution_state_unavailable",
        )
