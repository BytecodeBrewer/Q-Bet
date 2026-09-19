"""Post-event result collection composed with authoritative execution settlement."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from qbet.data.results import (
    ResultCollectionOutcome,
    ResultCollectionRequest,
    ResultCollectionStatus,
    ResultCollector,
)
from qbet.execution.models import ExecutionRecord, Lifecycle
from qbet.ledger import PortfolioLedger
from qbet.settlement import SettlementService
from qbet.storage.ledger import ExecutionStateRepository
from qbet.workflow.models import WorkflowMode


@dataclass(frozen=True)
class PostEventSettlementResult:
    collection: ResultCollectionOutcome
    record: ExecutionRecord
    ledger: PortfolioLedger
    settled: bool


class PostEventSettlementService:
    """Read one trackable post-event result and settle through the existing capital boundary."""

    def __init__(
        self,
        collector: ResultCollector,
        *,
        state_repository: ExecutionStateRepository | None = None,
        settlement_service: SettlementService | None = None,
    ) -> None:
        self._collector = collector
        self._state_repository = state_repository or ExecutionStateRepository()
        self._settlement_service = settlement_service or SettlementService()

    def collect_and_settle(self, request: ResultCollectionRequest) -> PostEventSettlementResult:
        record_id = _execution_id(request.execution_id)
        loaded = self._state_repository.load(record_id)
        if loaded is None:
            raise ValueError("result_execution_state_not_found")
        record, ledger = loaded
        self._validate_identity(record, request)

        if record.proposal.work.mode is not WorkflowMode.EXECUTION:
            raise ValueError("result_collection_execution_only")

        if record.state in {Lifecycle.SETTLED, Lifecycle.FAILED, Lifecycle.CANCELLED}:
            stored = record.collected_result
            if stored is None:
                raise ValueError("terminal_execution_missing_collected_result")
            outcome = ResultCollectionOutcome(
                request=request,
                status=ResultCollectionStatus.AVAILABLE,
                result=stored,
            )
            return PostEventSettlementResult(
                collection=outcome,
                record=record,
                ledger=ledger,
                settled=True,
            )

        if record.state is not Lifecycle.ACKNOWLEDGED:
            raise ValueError("result_collection_not_trackable")
        if record.result is None:
            raise ValueError("acknowledged_execution_missing_result")

        collection = self._collector.collect(request)
        if collection.status is not ResultCollectionStatus.AVAILABLE:
            return PostEventSettlementResult(
                collection=collection,
                record=record,
                ledger=ledger,
                settled=False,
            )

        settled_record, settled_ledger = self._settlement_service.settle(
            record,
            ledger,
            record.result,
            collected_result=collection,
        )
        if settled_record.error is not None:
            raise ValueError(settled_record.error)
        persisted_record, persisted_ledger = self._state_repository.persist(
            settled_record,
            settled_ledger,
        )
        return PostEventSettlementResult(
            collection=collection,
            record=persisted_record,
            ledger=persisted_ledger,
            settled=persisted_record.state
            in {Lifecycle.SETTLED, Lifecycle.FAILED, Lifecycle.CANCELLED},
        )

    @staticmethod
    def _validate_identity(record: ExecutionRecord, request: ResultCollectionRequest) -> None:
        work = record.proposal.work
        if (
            request.match_id != work.opportunity_id
            or request.execution_id != str(work.id)
            or request.correlation_id != work.correlation_id
        ):
            raise ValueError("collected_result_identity_mismatch")
        proposal = record.proposal
        if proposal.result_source is None or proposal.result_provider_target is None:
            raise ValueError("result_collection_target_unbound")
        if (
            request.source != proposal.result_source
            or request.provider_target != proposal.result_provider_target
        ):
            raise ValueError("collected_result_provider_identity_mismatch")


def _execution_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise ValueError("result_execution_id_invalid") from None