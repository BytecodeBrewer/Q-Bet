"""PostgreSQL persistence for restart-safe Smart Polling work."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.db.models import Q
from pydantic import ValidationError

from qbet.data.polling_runtime import PollingWork
from qbet.storage.models import PollingWorkRow


class PollingWorkPersistenceError(RuntimeError):
    """Stable storage failure that does not expose database details."""


def _identity(work: PollingWork) -> dict[str, object]:
    return {
        "provider_id": work.source.provider_id,
        "source_id": work.source.source_id,
        "target": work.target.value,
        "engine": work.engine,
        "mode": work.mode,
        "sport": work.sport,
        "match_id": work.match_id,
        "market": work.market,
    }


def _row_defaults(work: PollingWork) -> dict[str, object]:
    return {
        "correlation_id": work.correlation_id,
        "next_due_at": work.next_due_at,
        "attempt": work.attempt,
        "last_outcome": work.last_outcome or "",
        "last_reason": work.last_reason or "",
        "last_success_at": work.last_success_at,
        "terminal": work.terminal,
        "disabled": work.disabled,
        "payload": work.model_dump(mode="json"),
    }


class PostgresPollingWorkRepository:
    """Durable polling work queue with bounded, lease-protected claims."""

    def synchronize(self, active_work: Iterable[PollingWork]) -> tuple[PollingWork, ...]:
        configured = tuple(active_work)
        try:
            with transaction.atomic():
                for row in PollingWorkRow.objects.select_for_update().order_by("id"):
                    current = self._work_from_row(row)
                    if not current.disabled:
                        self._write_row(
                            row,
                            current.model_copy(update={"disabled": True}),
                            clear_claim=False,
                        )
                synchronized: list[PollingWork] = []
                for candidate in configured:
                    row = (
                        PollingWorkRow.objects.select_for_update()
                        .filter(**_identity(candidate))
                        .first()
                    )
                    if row is None:
                        PollingWorkRow.objects.create(
                            **_identity(candidate),
                            **_row_defaults(candidate),
                        )
                        synchronized.append(candidate)
                        continue

                    current = self._work_from_row(row)
                    # Recover parents scheduled by the old match-relative policy,
                    # without disturbing retry backoff or a successful refresh.
                    recover_discovery = (
                        current.discovery
                        and current.last_success_at is None
                        and current.attempt == 0
                        and current.last_outcome == "scheduled"
                        and current.last_reason != "discovery_refresh_due"
                    )
                    updated = current.model_copy(
                        update={
                            "source": candidate.source,
                            "correlation_id": candidate.correlation_id,
                            "sport": candidate.sport,
                            "market": candidate.market,
                            "event_starts_at": candidate.event_starts_at,
                            "disabled": candidate.disabled,
                            "discovery_limit": candidate.discovery_limit,
                            "terminal": current.terminal or candidate.terminal,
                            "next_due_at": candidate.next_due_at
                            if recover_discovery
                            else current.next_due_at,
                        }
                    )
                    self._write_row(row, updated, clear_claim=False)
                    synchronized.append(updated)
                return tuple(synchronized)
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingWorkPersistenceError("polling work persistence is unavailable") from error

    def claim_due(
        self,
        *,
        now,
        limit: int,
        lease_for: timedelta,
    ) -> tuple[PollingWork, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if lease_for <= timedelta():
            raise ValueError("lease_for must be positive")
        try:
            with transaction.atomic():
                rows = tuple(
                    PollingWorkRow.objects.select_for_update(skip_locked=True)
                    .filter(
                        disabled=False,
                        terminal=False,
                        next_due_at__lte=now,
                    )
                    .filter(Q(claim_until__isnull=True) | Q(claim_until__lte=now))
                    .order_by("next_due_at", "id")[:limit]
                )
                claim_until = now + lease_for
                claimed: list[PollingWork] = []
                for row in rows:
                    work = self._work_from_row(row)
                    row.claim_until = claim_until
                    row.save(update_fields=("claim_until", "updated_at"))
                    claimed.append(work)
                return tuple(claimed)
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingWorkPersistenceError("polling work persistence is unavailable") from error

    def save(self, work: PollingWork) -> PollingWork:
        try:
            with transaction.atomic():
                row = PollingWorkRow.objects.select_for_update().get(**_identity(work))
                self._write_row(row, work, clear_claim=True)
                from qbet.web.market_evaluation import enqueue_snapshot

                enqueue_snapshot(work)
                return work
        except PollingWorkRow.DoesNotExist as error:
            raise PollingWorkPersistenceError("polling work does not exist") from error
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingWorkPersistenceError("polling work persistence is unavailable") from error

    def load(
        self,
        *,
        provider_id: str,
        source_id: str,
        target: str,
        engine: str,
        mode: str,
        sport: str,
        match_id: str,
        market: str,
    ) -> PollingWork | None:
        try:
            row = PollingWorkRow.objects.filter(
                provider_id=provider_id,
                source_id=source_id,
                target=target,
                engine=engine,
                mode=mode,
                sport=sport,
                match_id=match_id,
                market=market,
            ).first()
            return None if row is None else self._work_from_row(row)
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingWorkPersistenceError("polling work persistence is unavailable") from error

    def list(self) -> tuple[PollingWork, ...]:
        try:
            rows = PollingWorkRow.objects.order_by("next_due_at", "id")
            return tuple(self._work_from_row(row) for row in rows)
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingWorkPersistenceError("polling work persistence is unavailable") from error

    @staticmethod
    def _write_row(
        row: PollingWorkRow,
        work: PollingWork,
        *,
        clear_claim: bool,
    ) -> None:
        for field, value in _row_defaults(work).items():
            setattr(row, field, value)
        if clear_claim:
            row.claim_until = None
        fields = [
            "correlation_id",
            "next_due_at",
            "attempt",
            "last_outcome",
            "last_reason",
            "last_success_at",
            "terminal",
            "disabled",
            "payload",
            "updated_at",
        ]
        if clear_claim:
            fields.append("claim_until")
        row.save(update_fields=tuple(fields))

    @staticmethod
    def _work_from_row(row: PollingWorkRow) -> PollingWork:
        work = PollingWork.model_validate(row.payload)
        expected = (
            work.source.provider_id,
            work.source.source_id,
            work.target.value,
            work.engine,
            work.mode,
            work.sport,
            work.match_id,
            work.market,
            work.correlation_id,
            work.next_due_at,
            work.attempt,
            work.last_outcome or "",
            work.last_reason or "",
            work.last_success_at,
            work.terminal,
            work.disabled,
        )
        stored = (
            row.provider_id,
            row.source_id,
            row.target,
            row.engine,
            row.mode,
            row.sport,
            row.match_id,
            row.market,
            row.correlation_id,
            row.next_due_at,
            row.attempt,
            row.last_outcome,
            row.last_reason,
            row.last_success_at,
            row.terminal,
            row.disabled,
        )
        if stored != expected:
            raise ValueError("polling work row metadata does not match typed payload")
        return work
