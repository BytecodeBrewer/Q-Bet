from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from django.test import TransactionTestCase

from qbet.data import (
    DataSourceMetadata,
    NormalizedMatchResult,
    ResultAvailability,
    ResultCollectionOutcome,
    ResultCollectionRequest,
    ResultCollectionStatus,
    ResultProviderTarget,
    ResultScore,
    SourceTransport,
)
from qbet.domain.ledger import LedgerOperation, PortfolioBalance
from qbet.execution.models import (
    ApprovedExecutionRequest,
    ExecutionRecord,
    Lifecycle,
    SandboxResult,
)
from qbet.ledger import PortfolioLedger
from qbet.settlement import ledger_command, transition
from qbet.settlement.post_event import PostEventSettlementService
from qbet.storage.ledger import ExecutionStateRepository
from qbet.workflow.models import WorkflowMode
from tests.unit.execution.test_service import proposal

NOW = datetime(2026, 9, 19, 18, 0, tzinfo=UTC)
RESULT_TIME = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)
SOURCE = DataSourceMetadata(
    provider_id="the_odds_api",
    source_id="the-odds-api-scores",
    transport=SourceTransport.API,
)
TARGET = ResultProviderTarget(sport="soccer_epl", event_id="event-123")


class RecordingCollector:
    def __init__(self, status: ResultCollectionStatus = ResultCollectionStatus.AVAILABLE) -> None:
        self.status = status
        self.requests: list[ResultCollectionRequest] = []

    def collect(self, request: ResultCollectionRequest) -> ResultCollectionOutcome:
        self.requests.append(request)
        if self.status is not ResultCollectionStatus.AVAILABLE:
            return ResultCollectionOutcome(
                request=request,
                status=self.status,
                reason_code="result_in_progress",
            )
        return ResultCollectionOutcome(
            request=request,
            status=ResultCollectionStatus.AVAILABLE,
            result=NormalizedMatchResult(
                match_id=request.match_id,
                execution_id=request.execution_id,
                correlation_id=request.correlation_id,
                source=request.source,
                availability=ResultAvailability.AVAILABLE,
                observed_at=RESULT_TIME,
                provider_target=request.provider_target,
                completed=True,
                scores=(
                    ResultScore(participant="Home", score=Decimal("0")),
                    ResultScore(participant="Away", score=Decimal("4")),
                ),
            ),
        )


class ExplodingCollector:
    def collect(self, request: ResultCollectionRequest) -> ResultCollectionOutcome:
        raise AssertionError("terminal replay must not call the provider")


def acknowledged_execution(*, mode: WorkflowMode = WorkflowMode.EXECUTION):
    base = proposal(expires_at=datetime(2026, 9, 20, tzinfo=UTC))
    work = base.work.model_copy(
        update={
            "mode": mode,
            "capital_context": mode.value,
        }
    )
    prepared = base.model_copy(update={"work": work})
    record = ExecutionRecord(proposal=prepared)
    approval = ApprovedExecutionRequest(
        proposal=prepared,
        approved_by="owner",
        approved_at=NOW,
    )
    record = transition(record, Lifecycle.APPROVED, approval=approval)
    ledger = PortfolioLedger(
        balance=PortfolioBalance(
            mode=mode.value,
            currency="EUR",
            available=Decimal("1000"),
        )
    )
    for operation, state in (
        (LedgerOperation.RESERVE, Lifecycle.APPROVED),
        (LedgerOperation.LOCK, Lifecycle.DISPATCHED),
        (LedgerOperation.PENDING, Lifecycle.ACKNOWLEDGED),
    ):
        updated, decision = ledger.apply(
            ledger_command(record, operation, prepared.capital_required)
        )
        assert decision.accepted
        ledger = updated
        if state is Lifecycle.DISPATCHED:
            record = transition(record, Lifecycle.DISPATCHED)
    sandbox_result = SandboxResult(
        dispatch_id=work.id,
        correlation_id=work.correlation_id,
        mode=mode.value,
        currency="EUR",
        payout=prepared.payout,
        status="success",
        observed_at=NOW,
    )
    record = transition(record, Lifecycle.ACKNOWLEDGED, result=sandbox_result)
    return record, ledger


def request_for(record: ExecutionRecord) -> ResultCollectionRequest:
    work = record.proposal.work
    return ResultCollectionRequest(
        match_id=work.opportunity_id,
        execution_id=str(work.id),
        correlation_id=work.correlation_id,
        source=SOURCE,
        fresh_after=NOW,
        provider_target=TARGET,
    )


class PostEventSettlementTests(TransactionTestCase):
    def test_final_score_settles_acknowledged_execution_once_without_treating_winner_as_financial_status(self) -> None:
        record, ledger = acknowledged_execution()
        repository = ExecutionStateRepository()
        repository.load_or_create(record, ledger)
        collector = RecordingCollector()
        request = request_for(record)

        first = PostEventSettlementService(
            collector,
            state_repository=repository,
        ).collect_and_settle(request)

        self.assertTrue(first.settled)
        self.assertEqual(first.record.state, Lifecycle.SETTLED)
        self.assertIsNotNone(first.record.collected_result)
        assert first.record.collected_result is not None
        self.assertEqual(first.record.collected_result.scores[1].score, Decimal("4"))
        # Away wins 4-0, but the score provider never controls the financial status.
        self.assertEqual(first.record.result.status, "success")
        self.assertEqual(first.record.result.observed_at, NOW)
        self.assertEqual(len(collector.requests), 1)

        replay = PostEventSettlementService(
            ExplodingCollector(),
            state_repository=ExecutionStateRepository(),
        ).collect_and_settle(request)

        self.assertTrue(replay.settled)
        self.assertEqual(replay.record, first.record)
        self.assertEqual(replay.ledger, first.ledger)

    def test_partial_result_does_not_mutate_authoritative_execution_or_ledger(self) -> None:
        record, ledger = acknowledged_execution()
        repository = ExecutionStateRepository()
        repository.load_or_create(record, ledger)
        collector = RecordingCollector(ResultCollectionStatus.PARTIAL)

        result = PostEventSettlementService(
            collector,
            state_repository=repository,
        ).collect_and_settle(request_for(record))

        self.assertFalse(result.settled)
        self.assertEqual(result.collection.status, ResultCollectionStatus.PARTIAL)
        restored = repository.load(record.proposal.work.id)
        self.assertIsNotNone(restored)
        assert restored is not None
        restored_record, restored_ledger = restored
        self.assertEqual(restored_record.state, Lifecycle.ACKNOWLEDGED)
        self.assertEqual(restored_record.collected_result, None)
        self.assertEqual(restored_ledger, ledger)

    def test_non_trackable_execution_is_rejected_before_provider_call(self) -> None:
        base = ExecutionRecord(proposal=proposal())
        repository = ExecutionStateRepository()
        ledger = PortfolioLedger(
            balance=PortfolioBalance(
                mode="execution",
                currency="EUR",
                available=Decimal("1000"),
            )
        )
        repository.load_or_create(base, ledger)
        collector = RecordingCollector()

        with self.assertRaisesMessage(ValueError, "result_collection_not_trackable"):
            PostEventSettlementService(
                collector,
                state_repository=repository,
            ).collect_and_settle(request_for(base))

        self.assertEqual(collector.requests, [])

    def test_identity_mismatch_is_rejected_before_provider_call(self) -> None:
        record, ledger = acknowledged_execution()
        repository = ExecutionStateRepository()
        repository.load_or_create(record, ledger)
        collector = RecordingCollector()
        wrong = request_for(record).model_copy(update={"match_id": "other-opportunity"})

        with self.assertRaisesMessage(ValueError, "collected_result_identity_mismatch"):
            PostEventSettlementService(
                collector,
                state_repository=repository,
            ).collect_and_settle(wrong)

        self.assertEqual(collector.requests, [])

    def test_simulation_execution_record_cannot_use_post_event_settlement_service(self) -> None:
        record, ledger = acknowledged_execution(mode=WorkflowMode.SIMULATION)
        repository = ExecutionStateRepository()
        repository.load_or_create(record, ledger)
        collector = RecordingCollector()

        with self.assertRaisesMessage(ValueError, "result_collection_execution_only"):
            PostEventSettlementService(
                collector,
                state_repository=repository,
            ).collect_and_settle(request_for(record))

        self.assertEqual(collector.requests, [])