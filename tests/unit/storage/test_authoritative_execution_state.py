from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase

from qbet.calculations.qualifying_bet import QualifyingBetInput
from qbet.domain.ledger import PortfolioBalance
from qbet.engines import BonusEngineRequest
from qbet.execution.models import ExecutionProposal, ExecutionRecord
from qbet.execution.sandbox import valuation
from qbet.ledger import PortfolioLedger
from qbet.storage.ledger import ExecutionStateRepository
from qbet.workflow.models import WorkflowMode
from qbet.workflow.routing import RoutedWorkItem


class AuthoritativeExecutionStateTests(TestCase):
    def _record(self) -> ExecutionRecord:
        request = BonusEngineRequest(
            opportunity_id="authoritative-state",
            inputs=QualifyingBetInput(
                back_odds=Decimal("2"), lay_odds=Decimal("2.1"),
                back_stake=Decimal("10"), exchange_commission=Decimal("0.02"),
                stake_precision=Decimal("1"), max_lay_liability=Decimal("100"),
            ),
            currency="EUR", execution_offer_ids=("back", "lay"),
            generated_at=datetime.now(UTC),
        )
        capital, payout = valuation(request)
        work = RoutedWorkItem(
            id=uuid4(), correlation_id=uuid4(), engine="bonus", mode=WorkflowMode.EXECUTION,
            opportunity_id=request.opportunity_id, capital_context="execution",
        )
        return ExecutionRecord(proposal=ExecutionProposal(
            work=work, request=request, expires_at=datetime.now(UTC) + timedelta(minutes=5),
            currency="EUR", capital_required=capital, payout=payout,
        ))

    @staticmethod
    def _ledger() -> PortfolioLedger:
        return PortfolioLedger(balance=PortfolioBalance(
            mode="execution", currency="EUR", available=Decimal("1000")
        ))

    def test_reopens_persisted_pair_and_rejects_conflicting_record(self) -> None:
        repository = ExecutionStateRepository()
        record = self._record()
        repository.load_or_create(record, self._ledger())

        reopened_record, reopened_ledger = repository.load_or_create(record, self._ledger())
        self.assertEqual(reopened_record, record)
        self.assertEqual(reopened_ledger, self._ledger())

        conflicting = record.model_copy(update={"proposal": record.proposal.model_copy(
            update={"work": record.proposal.work.model_copy(update={"opportunity_id": "other"})}
        )})
        with self.assertRaisesMessage(ValueError, "execution_record_conflict"):
            repository.load_or_create(conflicting, self._ledger())

    def test_persist_updates_ledger_and_record_together(self) -> None:
        repository = ExecutionStateRepository()
        record = self._record()
        persisted_record, persisted_ledger = repository.load_or_create(record, self._ledger())
        repository.persist(persisted_record, persisted_ledger)

        reopened_record, reopened_ledger = repository.load_or_create(record, self._ledger())
        self.assertEqual(reopened_record, record)
        self.assertEqual(reopened_ledger, self._ledger())
