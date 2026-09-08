"""PostgreSQL integration coverage for reconstructable administrator Monitoring traces."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from django.db import connection
from django.test import TransactionTestCase

from qbet.calculations.qualifying_bet import QualifyingBetInput
from qbet.engines import BonusEngineRequest
from qbet.monitoring import MonitoringQuery, MonitoringRecord
from qbet.request_handler import (
    ExecutionSandboxRequestHandler,
    ModeRequestHandlers,
    RevalidationOutcome,
    ResultStatus,
    SandboxResultFixture,
    SandboxRevalidationFixture,
    SimulationSandboxRequestHandler,
)
from qbet.storage.models import (
    ExecutionRecordRow,
    ModeWorkQueueRow,
    MonitoringRecordRow,
    PortfolioLedgerRow,
    SimulationReportRow,
)
from qbet.storage.monitoring import MonitoringPersistenceError, PostgresMonitoringRepository
from qbet.workflow import WorkflowMode, WorkState
from qbet.workflow.dispatch import ModeDispatchCoordinator
from qbet.workflow.routing import EngineModes, RoutingConfiguration

CORRELATION_ID = UUID("12345678-1234-5678-1234-567812345678")


def _bonus_request(now: datetime, opportunity_id: str = "monitoring-bonus") -> BonusEngineRequest:
    return BonusEngineRequest(
        opportunity_id=opportunity_id,
        inputs=QualifyingBetInput(
            back_odds=Decimal("2.5"),
            lay_odds=Decimal("2.6"),
            back_stake=Decimal("10"),
            exchange_commission=Decimal("0.02"),
            stake_precision=Decimal("0.01"),
            max_lay_liability=Decimal("100"),
        ),
        currency="EUR",
        execution_offer_ids=("book", "exchange"),
        generated_at=now,
    )


def _handlers(opportunity_id: str, now: datetime) -> ModeRequestHandlers:
    revalidation = SandboxRevalidationFixture(
        opportunity_id=opportunity_id,
        outcome=RevalidationOutcome.VALID,
        validated_at=now,
    )
    result = SandboxResultFixture(
        opportunity_id=opportunity_id,
        status=ResultStatus.SUCCESS,
        observed_at=now,
        result_reference="sandbox-result",
    )
    return ModeRequestHandlers(
        simulation=SimulationSandboxRequestHandler(
            revalidation_fixtures=(revalidation,),
            result_fixtures=(result,),
        ),
        execution=ExecutionSandboxRequestHandler(
            revalidation_fixtures=(revalidation,),
            result_fixtures=(result,),
        ),
    )


class _FailOnceSettlementMonitoringRepository(PostgresMonitoringRepository):
    def __init__(self) -> None:
        self.failed = False

    def append(self, record: MonitoringRecord) -> MonitoringRecord:
        if (
            not self.failed
            and record.stage == "settlement"
            and record.event_type == "capital_transition"
        ):
            self.failed = True
            raise OSError("injected monitoring persistence failure")
        return super().append(record)


class _FailFirstMonitoringRepository(PostgresMonitoringRepository):
    def __init__(self) -> None:
        self.failed = False

    def append(self, record: MonitoringRecord) -> MonitoringRecord:
        if not self.failed:
            self.failed = True
            raise MonitoringPersistenceError("injected early monitoring failure")
        return super().append(record)


class DispatchMonitoringPostgresTests(TransactionTestCase):
    def _records(self, correlation_id: UUID, started_at: datetime):
        return PostgresMonitoringRepository().list_records(
            MonitoringQuery(
                start=started_at - timedelta(minutes=1),
                end=datetime.now(UTC) + timedelta(minutes=1),
                correlation_id=correlation_id,
            )
        )

    def _coordinator(
        self,
        *,
        simulation: bool = False,
        execution: bool = False,
        now: datetime,
        monitoring_writer: PostgresMonitoringRepository | None = None,
    ) -> ModeDispatchCoordinator:
        request = _bonus_request(now)
        return ModeDispatchCoordinator(
            RoutingConfiguration(
                bonus=EngineModes(simulation=simulation, execution=execution)
            ),
            mode_request_handlers=_handlers(request.opportunity_id, now),
            monitoring_writer=monitoring_writer,
        )

    def _schedule_and_dispatch(
        self,
        coordinator: ModeDispatchCoordinator,
        *,
        now: datetime,
    ):
        request = _bonus_request(now)
        scheduled = coordinator.schedule(
            request,
            owner="owner",
            correlation_id=CORRELATION_ID,
            scheduled_for=now,
            expires_at=now + timedelta(minutes=5),
        )
        dispatched = coordinator.dispatch_due(now=now, owner="owner")
        return scheduled, dispatched

    def test_execution_trace_reconstructs_lifecycle_capital_settlement_and_queue(self) -> None:
        now = datetime.now(UTC)
        coordinator = self._coordinator(execution=True, now=now)
        scheduled, dispatched = self._schedule_and_dispatch(coordinator, now=now)

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0].work.mode, WorkflowMode.EXECUTION)
        self.assertEqual(tuple(item.state for item in dispatched), (WorkState.COMPLETED,))
        self.assertEqual(ExecutionRecordRow.objects.get().state, "settled")
        self.assertEqual(PortfolioLedgerRow.objects.get().mode, "execution")

        records = self._records(CORRELATION_ID, now)
        lifecycle = [
            record.status
            for record in records
            if record.stage == "execution" and record.event_type == "lifecycle_transition"
        ]
        capital = [record.reason_code for record in records if record.event_type == "capital_transition"]
        self.assertEqual(
            lifecycle,
            ["proposed", "awaiting_approval", "approved", "dispatched", "acknowledged", "settled"],
        )
        self.assertEqual(capital, ["reserve", "lock", "pending", "settle"])
        settlement = next(
            record for record in records if record.stage == "settlement" and record.event_type == "result"
        )
        self.assertEqual(settlement.status, "success")
        self.assertEqual(settlement.references["settlement_id"], str(scheduled[0].work.id))
        self.assertTrue(any(record.stage == "queue" and record.status == "completed" for record in records))
        self.assertEqual(
            [record.occurred_at for record in records],
            sorted(record.occurred_at for record in records),
        )

    def test_simulation_trace_contains_ledger_settlement_report_and_subprocess_references(self) -> None:
        now = datetime.now(UTC)
        coordinator = self._coordinator(simulation=True, now=now)
        _, dispatched = self._schedule_and_dispatch(coordinator, now=now)

        self.assertEqual(tuple(item.state for item in dispatched), (WorkState.COMPLETED,))
        self.assertEqual(SimulationReportRow.objects.count(), 1)
        self.assertEqual(PortfolioLedgerRow.objects.get().mode, "simulation")

        records = self._records(CORRELATION_ID, now)
        self.assertTrue(
            any(
                record.stage == "simulation"
                and record.event_type == "lifecycle"
                and record.references.get("report_id")
                for record in records
            )
        )
        self.assertEqual(
            [record.reason_code for record in records if record.event_type == "capital_transition"],
            ["reserve", "lock", "pending", "settle"],
        )
        subprocess_ids = {
            record.references.get("subprocess_id")
            for record in records
            if record.event_type in {"stage", "refresh"} and record.references.get("subprocess_id")
        }
        self.assertGreaterEqual(len(subprocess_ids), 2)
        self.assertTrue(any(record.stage == "queue" and record.status == "completed" for record in records))

    def test_mode_exception_persists_safe_error_and_terminal_failed_queue_state(self) -> None:
        now = datetime.now(UTC)
        coordinator = self._coordinator(simulation=True, now=now)
        with patch(
            "qbet.workflow.dispatch.WorkflowSimulationRunner.run",
            side_effect=RuntimeError("secret runtime detail must never persist"),
        ):
            _, dispatched = self._schedule_and_dispatch(coordinator, now=now)

        self.assertEqual(tuple(item.state for item in dispatched), (WorkState.FAILED,))
        self.assertEqual(ModeWorkQueueRow.objects.get().state, "failed")
        self.assertEqual(SimulationReportRow.objects.count(), 0)
        records = self._records(CORRELATION_ID, now)
        error = next(record for record in records if record.event_type == "lifecycle_error")
        self.assertEqual(error.reason_code, "mode_execution_failed")
        stored_payloads = list(MonitoringRecordRow.objects.values_list("payload", flat=True))
        self.assertNotIn("secret runtime detail", str(stored_payloads))
        self.assertNotIn("Traceback", str(stored_payloads))

    def test_monitoring_projection_failure_rolls_back_authoritative_execution_pair(self) -> None:
        now = datetime.now(UTC)
        writer = _FailOnceSettlementMonitoringRepository()
        coordinator = self._coordinator(execution=True, now=now, monitoring_writer=writer)
        _, dispatched = self._schedule_and_dispatch(coordinator, now=now)

        self.assertTrue(writer.failed)
        self.assertEqual(tuple(item.state for item in dispatched), (WorkState.FAILED,))
        self.assertEqual(ModeWorkQueueRow.objects.get().state, "failed")
        self.assertEqual(ExecutionRecordRow.objects.count(), 0)
        self.assertEqual(PortfolioLedgerRow.objects.count(), 0)
        records = self._records(CORRELATION_ID, now)
        self.assertFalse(any(record.event_type == "capital_transition" for record in records))
        self.assertFalse(any(record.event_type == "lifecycle_transition" for record in records))
        self.assertTrue(any(record.stage == "queue" and record.status == "failed" for record in records))

    def test_early_monitoring_failure_never_strands_claimed_work_in_processing(self) -> None:
        now = datetime.now(UTC)
        writer = _FailFirstMonitoringRepository()
        coordinator = self._coordinator(execution=True, now=now, monitoring_writer=writer)
        _, dispatched = self._schedule_and_dispatch(coordinator, now=now)

        self.assertTrue(writer.failed)
        self.assertEqual(tuple(item.state for item in dispatched), (WorkState.FAILED,))
        queue_row = ModeWorkQueueRow.objects.get()
        self.assertEqual(queue_row.state, "failed")
        self.assertNotEqual(queue_row.state, "processing")
        records = self._records(CORRELATION_ID, now)
        self.assertTrue(
            any(
                record.event_type == "lifecycle_error"
                and record.reason_code == "monitoring_persistence_unavailable"
                for record in records
            )
        )

    def test_monitoring_table_uses_postgresql_row_level_security(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relrowsecurity FROM pg_class WHERE relname = 'qbet_monitoring_records'"
            )
            row = cursor.fetchone()

        self.assertIsNotNone(row)
        assert row is not None
        self.assertTrue(row[0])
