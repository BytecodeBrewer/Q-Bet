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
from qbet.monitoring import MonitoringLevel, MonitoringRecord
from qbet.settlement import SettlementService
from qbet.storage.ledger import ExecutionStateRepository, ModeWorkQueueRepository
from qbet.storage.monitoring import MonitoringPersistenceError, PostgresMonitoringRepository
from qbet.workflow.models import WorkflowMode
from qbet.workflow.queue import WorkState


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
        queue_repository: ModeWorkQueueRepository | None = None,
        monitoring_writer: PostgresMonitoringRepository | None = None,
    ) -> None:
        self._collector = collector
        self._state_repository = state_repository or ExecutionStateRepository()
        self._settlement_service = settlement_service or SettlementService()
        self._queue_repository = queue_repository or ModeWorkQueueRepository()
        self._monitoring_writer = monitoring_writer or PostgresMonitoringRepository()

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
            self._finalize_tracking(record, outcome)
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
        self._finalize_tracking(persisted_record, collection)
        return PostEventSettlementResult(
            collection=collection,
            record=persisted_record,
            ledger=persisted_ledger,
            settled=persisted_record.state
            in {Lifecycle.SETTLED, Lifecycle.FAILED, Lifecycle.CANCELLED},
        )

    def _finalize_tracking(
        self,
        record: ExecutionRecord,
        collection: ResultCollectionOutcome,
    ) -> None:
        final_state = {
            Lifecycle.SETTLED: WorkState.COMPLETED,
            Lifecycle.FAILED: WorkState.FAILED,
            Lifecycle.CANCELLED: WorkState.CANCELLED,
        }.get(record.state)
        if final_state is None:
            return

        work = record.proposal.work
        queued = self._queue_repository.load(work.id)
        if queued is None or queued.state is final_state:
            return
        if queued.state is not WorkState.RECHECK:
            return

        result = collection.result or record.collected_result
        occurred_at = result.observed_at if result is not None else collection.request.fresh_after
        finalized = self._queue_repository.save(
            queued.transition(
                final_state,
                now=occurred_at,
                reason=f"post_event_{record.state.value}",
            )
        )
        try:
            self._monitoring_writer.append(
                MonitoringRecord(
                    correlation_id=work.correlation_id,
                    occurred_at=occurred_at,
                    engine=work.engine,
                    mode=work.mode.value,
                    stage="settlement",
                    event_type="post_event_result",
                    status=record.state.value,
                    reason_code=f"post_event_{record.state.value}",
                    level=(
                        MonitoringLevel.INFO
                        if record.state is Lifecycle.SETTLED
                        else MonitoringLevel.WARNING
                    ),
                    references={
                        "work_id": str(work.id),
                        "opportunity_id": work.opportunity_id,
                        "queue_state": finalized.state.value,
                    },
                )
            )
        except MonitoringPersistenceError:
            # Monitoring is observational and must never roll back authoritative state.
            pass

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