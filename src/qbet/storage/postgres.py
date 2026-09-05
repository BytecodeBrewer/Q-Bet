"""PostgreSQL-backed operational persistence adapters for Q-Bet."""

from __future__ import annotations

from uuid import UUID

from django.db import DatabaseError, transaction

from qbet.domain.models import Identifier
from qbet.domain.verification import ProviderState
from qbet.layers.logging import SimulationLogRecord
from qbet.reporting import SimulationReport
from qbet.storage.models import ProviderStateRow, SimulationRecordRow, SimulationReportRow


class PostgresSimulationReportReader:
    """Read durable simulation history from the configured PostgreSQL database."""

    def load_report(self, run_id: UUID) -> SimulationReport:
        try:
            row = SimulationReportRow.objects.filter(run_id=run_id).values_list("payload", flat=True).first()
        except DatabaseError as error:
            raise OSError("simulation history is unavailable") from error
        if row is None:
            raise KeyError(f"simulation report {run_id} was not found")
        return SimulationReport.model_validate_json(row)

    def load_records(self, run_id: UUID) -> tuple[SimulationLogRecord, ...]:
        try:
            rows = tuple(
                SimulationRecordRow.objects.filter(run_id=run_id)
                .order_by("sequence")
                .values_list("payload", flat=True)
            )
        except DatabaseError as error:
            raise OSError("simulation history is unavailable") from error
        if not rows:
            raise KeyError(f"simulation records for {run_id} were not found")
        return tuple(SimulationLogRecord.model_validate_json(payload) for payload in rows)

    def list_recent_reports(self, limit: int = 20) -> tuple[SimulationReport, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        try:
            rows = tuple(
                SimulationReportRow.objects.order_by("-generated_at")
                .values_list("payload", flat=True)[:limit]
            )
        except DatabaseError as error:
            raise OSError("simulation history is unavailable") from error
        return tuple(SimulationReport.model_validate_json(payload) for payload in rows)


class PostgresSimulationReportStore(PostgresSimulationReportReader):
    """Persist simulation logs and compact reports in PostgreSQL."""

    def append_records(self, records: tuple[SimulationLogRecord, ...]) -> None:
        if not records:
            return
        rows = [
            SimulationRecordRow(
                run_id=record.run_id,
                sequence=record.sequence,
                payload=record.model_dump_json(),
            )
            for record in records
        ]
        try:
            SimulationRecordRow.objects.bulk_create(rows, ignore_conflicts=True)
        except DatabaseError as error:
            raise OSError("simulation history is unavailable") from error

    def finalize_run(self, report: SimulationReport) -> None:
        try:
            SimulationReportRow.objects.update_or_create(
                run_id=report.run_id,
                defaults={
                    "generated_at": report.generated_at,
                    "payload": report.model_dump_json(),
                },
            )
        except DatabaseError as error:
            raise OSError("simulation history is unavailable") from error


class PostgresProviderStateRepository:
    """Persist provider operational state in the shared PostgreSQL source of truth."""

    def get(self, provider_id: Identifier) -> ProviderState | None:
        try:
            row = ProviderStateRow.objects.filter(provider_id=str(provider_id)).first()
        except DatabaseError as error:
            raise OSError("provider state is unavailable") from error
        if row is None:
            return None
        return ProviderState(
            provider_id=row.provider_id,
            active_bets_count=row.active_bets_count,
            last_bet_timestamp=row.last_bet_timestamp,
            is_cooldown_active=row.is_cooldown_active,
        )

    def upsert(self, state: ProviderState) -> None:
        try:
            with transaction.atomic():
                ProviderStateRow.objects.update_or_create(
                    provider_id=str(state.provider_id),
                    defaults={
                        "active_bets_count": state.active_bets_count,
                        "last_bet_timestamp": state.last_bet_timestamp,
                        "is_cooldown_active": state.is_cooldown_active,
                    },
                )
        except DatabaseError as error:
            raise OSError("provider state is unavailable") from error
