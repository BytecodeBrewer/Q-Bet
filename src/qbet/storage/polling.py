"""PostgreSQL persistence for provider-aware Smart Polling strategies."""

from __future__ import annotations

from django.db import DatabaseError, transaction
from pydantic import ValidationError

from qbet.data.polling import (
    PollingStrategy,
    PollingStrategyResolver,
    PollingTarget,
)
from qbet.domain.models import Identifier
from qbet.storage.models import PollingStrategyRow


class PollingStrategyPersistenceError(RuntimeError):
    """Stable storage failure that does not leak database details to callers."""


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
