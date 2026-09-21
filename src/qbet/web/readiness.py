"""Read-only operational readiness for Q-Bet's authoritative PostgreSQL state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from django.db import connection
from django.db.migrations.exceptions import InconsistentMigrationHistory, NodeNotFoundError
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import DatabaseError


class PersistenceReadinessCode(StrEnum):
    READY = "ready"
    DATABASE_UNAVAILABLE = "database_unavailable"
    MIGRATIONS_PENDING = "migrations_pending"
    MIGRATION_STATE_INVALID = "migration_state_invalid"


@dataclass(frozen=True)
class PersistenceReadiness:
    code: PersistenceReadinessCode

    @property
    def ready(self) -> bool:
        return self.code is PersistenceReadinessCode.READY


def persistence_readiness() -> PersistenceReadiness:
    """Inspect database connectivity and migration state without mutating either."""

    try:
        connection.ensure_connection()
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        pending = executor.migration_plan(targets)
    except DatabaseError:
        return PersistenceReadiness(PersistenceReadinessCode.DATABASE_UNAVAILABLE)
    except (InconsistentMigrationHistory, NodeNotFoundError):
        return PersistenceReadiness(PersistenceReadinessCode.MIGRATION_STATE_INVALID)

    if pending:
        return PersistenceReadiness(PersistenceReadinessCode.MIGRATIONS_PENDING)
    return PersistenceReadiness(PersistenceReadinessCode.READY)
