"""PostgreSQL persistence for provider-aware Smart Polling strategies."""

from __future__ import annotations

from datetime import timedelta

from django.db import DatabaseError, IntegrityError, transaction
from pydantic import ValidationError

from qbet.data.polling import (
    PollingStrategy,
    PollingStrategyResolver,
    PollingTarget,
)
from qbet.data.polling_work import PollingWorkItem, PollingWorkState
from qbet.domain.models import Identifier
from qbet.storage.models import PollingStrategyRow, PollingWorkRow


class PollingStrategyPersistenceError(RuntimeError):
    """Stable storage failure that does not leak database details to callers."""


class PollingWorkPersistenceError(RuntimeError):
    """Stable durable-work failure that does not expose database details."""


def _engine_key(engine: str | None) -> str:
    return engine or ""


class PollingStrategyRepository:
    """Typed operational repository backed only by PostgreSQL/Django ORM."""

    def save(self, strategy: PollingStrategy) -> PollingStrategy:
        try:
            PollingStrategyRow.objects.update_or_create(
                provider_id=strategy.source.provider_id,
                source_id=strategy.source.source_id,
                target=strategy.target.value,
                engine=_engine_key(strategy.engine),
                defaults={
                    "enabled": strategy.enabled,
                    "payload": strategy.model_dump(mode="json"),
                },
            )
        except DatabaseError as error:
            raise PollingStrategyPersistenceError(
                "polling strategy configuration is unavailable"
            ) from error
        return strategy

    def load(
        self,
        *,
        provider_id: Identifier,
        source_id: Identifier,
        target: PollingTarget,
        engine: Identifier | None,
    ) -> PollingStrategy | None:
        try:
            row = PollingStrategyRow.objects.filter(
                provider_id=provider_id,
                source_id=source_id,
                target=target.value,
                engine=_engine_key(engine),
            ).first()
            if row is None:
                return None
            return self._strategy_from_row(row)
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingStrategyPersistenceError(
                "polling strategy configuration is unavailable"
            ) from error

    def list(self) -> tuple[PollingStrategy, ...]:
        try:
            rows = PollingStrategyRow.objects.order_by(
                "provider_id", "source_id", "target", "engine"
            )
            return tuple(self._strategy_from_row(row) for row in rows)
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingStrategyPersistenceError(
                "polling strategy configuration is unavailable"
            ) from error

    def resolver(self) -> PollingStrategyResolver:
        """Return only validated domain strategies to the pure polling policy."""

        return PollingStrategyResolver(self.list())

    def set_enabled(
        self,
        *,
        provider_id: Identifier,
        source_id: Identifier,
        target: PollingTarget,
        engine: Identifier | None,
        enabled: bool,
    ) -> PollingStrategy:
        try:
            with transaction.atomic():
                row = PollingStrategyRow.objects.select_for_update().get(
                    provider_id=provider_id,
                    source_id=source_id,
                    target=target.value,
                    engine=_engine_key(engine),
                )
                current = self._strategy_from_row(row)
                updated = current.model_copy(update={"enabled": enabled})
                row.enabled = updated.enabled
                row.payload = updated.model_dump(mode="json")
                row.save(update_fields=("enabled", "payload", "updated_at"))
                return updated
        except PollingStrategyRow.DoesNotExist as error:
            raise PollingStrategyPersistenceError("polling strategy does not exist") from error
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingStrategyPersistenceError(
                "polling strategy configuration is unavailable"
            ) from error

    @staticmethod
    def _strategy_from_row(row: PollingStrategyRow) -> PollingStrategy:
        strategy = PollingStrategy.model_validate(row.payload)
        expected = (
            strategy.source.provider_id,
            strategy.source.source_id,
            strategy.target.value,
            _engine_key(strategy.engine),
            strategy.enabled,
        )
        stored = (row.provider_id, row.source_id, row.target, row.engine, row.enabled)
        if stored != expected:
            raise ValueError("polling strategy row metadata does not match typed payload")
        return strategy


class PollingWorkRepository:
    """Restart-safe queue/state store for bounded hosted Smart Polling ticks."""

    def ensure(self, work: PollingWorkItem) -> PollingWorkItem:
        """Create one identity once, returning durable state on repeated seeding."""

        try:
            with transaction.atomic():
                row = (
                    PollingWorkRow.objects.select_for_update()
                    .filter(identity_key=work.identity_key)
                    .first()
                )
                if row is not None:
                    return self._work_from_row(row)
                PollingWorkRow.objects.create(
                    work_id=work.id,
                    identity_key=work.identity_key,
                    correlation_id=work.request.correlation_id,
                    provider_id=work.request.source.provider_id,
                    source_id=work.request.source.source_id,
                    target=work.request.target.value,
                    engine=work.request.engine,
                    mode=work.request.mode,
                    owner=work.owner,
                    state=work.state.value,
                    next_due_at=work.next_due_at,
                    claimed_at=work.claimed_at,
                    payload=work.model_dump(mode="json"),
                )
                return work
        except IntegrityError:
            try:
                row = PollingWorkRow.objects.get(identity_key=work.identity_key)
                return self._work_from_row(row)
            except (DatabaseError, PollingWorkRow.DoesNotExist, ValidationError, ValueError) as error:
                raise PollingWorkPersistenceError("polling work is unavailable") from error
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingWorkPersistenceError("polling work is unavailable") from error

    def save(self, work: PollingWorkItem) -> PollingWorkItem:
        try:
            with transaction.atomic():
                row = PollingWorkRow.objects.select_for_update().get(work_id=work.id)
                current = self._work_from_row(row)
                if current.identity_key != work.identity_key:
                    raise ValueError("polling work identity cannot change")
                self._apply(row, work)
                return work
        except PollingWorkRow.DoesNotExist as error:
            raise PollingWorkPersistenceError("polling work does not exist") from error
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingWorkPersistenceError("polling work is unavailable") from error

    def load(self, work_id) -> PollingWorkItem | None:
        try:
            row = PollingWorkRow.objects.filter(work_id=work_id).first()
            return None if row is None else self._work_from_row(row)
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingWorkPersistenceError("polling work is unavailable") from error

    def claim_due(
        self,
        *,
        now,
        limit: int,
        lease: timedelta = timedelta(minutes=5),
    ) -> tuple[PollingWorkItem, ...]:
        """Claim bounded due rows and recover abandoned processing leases."""

        if limit < 1:
            return ()
        try:
            with transaction.atomic():
                stale = tuple(
                    PollingWorkRow.objects.select_for_update(skip_locked=True)
                    .filter(
                        state=PollingWorkState.PROCESSING.value,
                        claimed_at__lte=now - lease,
                    )
                    .order_by("claimed_at", "work_id")[:limit]
                )
                for row in stale:
                    item = self._work_from_row(row).model_copy(
                        update={
                            "state": PollingWorkState.PENDING,
                            "claimed_at": None,
                            "next_due_at": now,
                            "last_outcome": "deferred",
                            "last_reason": "polling_claim_recovered",
                        }
                    )
                    self._apply(row, item)

                rows = tuple(
                    PollingWorkRow.objects.select_for_update(skip_locked=True)
                    .filter(
                        state=PollingWorkState.PENDING.value,
                        next_due_at__lte=now,
                    )
                    .order_by("next_due_at", "work_id")[:limit]
                )
                claimed: list[PollingWorkItem] = []
                for row in rows:
                    item = self._work_from_row(row).model_copy(
                        update={
                            "state": PollingWorkState.PROCESSING,
                            "claimed_at": now,
                        }
                    )
                    self._apply(row, item)
                    claimed.append(item)
                return tuple(claimed)
        except (DatabaseError, ValidationError, ValueError) as error:
            raise PollingWorkPersistenceError("polling work is unavailable") from error

    @staticmethod
    def _apply(row: PollingWorkRow, work: PollingWorkItem) -> None:
        row.correlation_id = work.request.correlation_id
        row.provider_id = work.request.source.provider_id
        row.source_id = work.request.source.source_id
        row.target = work.request.target.value
        row.engine = work.request.engine
        row.mode = work.request.mode
        row.owner = work.owner
        row.state = work.state.value
        row.next_due_at = work.next_due_at
        row.claimed_at = work.claimed_at
        row.payload = work.model_dump(mode="json")
        row.save(
            update_fields=(
                "correlation_id",
                "provider_id",
                "source_id",
                "target",
                "engine",
                "mode",
                "owner",
                "state",
                "next_due_at",
                "claimed_at",
                "payload",
                "updated_at",
            )
        )

    @staticmethod
    def _work_from_row(row: PollingWorkRow) -> PollingWorkItem:
        work = PollingWorkItem.model_validate(row.payload)
        expected = (
            work.id,
            work.identity_key,
            work.request.correlation_id,
            work.request.source.provider_id,
            work.request.source.source_id,
            work.request.target.value,
            work.request.engine,
            work.request.mode,
            work.owner,
            work.state.value,
            work.next_due_at,
            work.claimed_at,
        )
        stored = (
            row.work_id,
            row.identity_key,
            row.correlation_id,
            row.provider_id,
            row.source_id,
            row.target,
            row.engine,
            row.mode,
            row.owner,
            row.state,
            row.next_due_at,
            row.claimed_at,
        )
        if stored != expected:
            raise ValueError("polling work row metadata does not match typed payload")
        return work
