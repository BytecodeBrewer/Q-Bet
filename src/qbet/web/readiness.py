"""Read-only operational readiness for Q-Bet's authoritative PostgreSQL state."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum

from django.db import connection
from django.db.migrations.exceptions import InconsistentMigrationHistory, NodeNotFoundError
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import DatabaseError

from qbet.storage.ledger import (
    RoutingConfigurationPersistenceError,
    RoutingConfigurationRepository,
)
from qbet.storage.models import ExecutionRecordRow, MonitoringRecordRow

_RELEASE_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


class PersistenceReadinessCode(StrEnum):
    READY = "ready"
    DATABASE_UNAVAILABLE = "database_unavailable"
    MIGRATIONS_PENDING = "migrations_pending"
    MIGRATION_STATE_INVALID = "migration_state_invalid"
    ROUTING_UNAVAILABLE = "routing_unavailable"
    APPROVALS_UNAVAILABLE = "approvals_unavailable"
    MONITORING_UNAVAILABLE = "monitoring_unavailable"


@dataclass(frozen=True)
class PersistenceReadiness:
    code: PersistenceReadinessCode
    routing: str = "unverified"
    approvals: str = "unverified"
    monitoring: str = "unverified"

    @property
    def ready(self) -> bool:
        return self.code is PersistenceReadinessCode.READY

    @property
    def persistence(self) -> str:
        if self.code in {
            PersistenceReadinessCode.DATABASE_UNAVAILABLE,
            PersistenceReadinessCode.MIGRATIONS_PENDING,
            PersistenceReadinessCode.MIGRATION_STATE_INVALID,
        }:
            return self.code.value
        return PersistenceReadinessCode.READY.value

    @property
    def runtime(self) -> dict[str, str]:
        return {
            "routing": self.routing,
            "approvals": self.approvals,
            "monitoring": self.monitoring,
        }


def deployment_release_id() -> str:
    """Return the immutable reviewed Git SHA when the deployment provides one."""

    for name in ("QBET_RELEASE_SHA", "VERCEL_GIT_COMMIT_SHA", "GITHUB_SHA"):
        value = os.environ.get(name, "").strip()
        if _RELEASE_SHA.fullmatch(value):
            return value.lower()
    return "unknown"


def _routing_readable() -> bool:
    try:
        RoutingConfigurationRepository().load()
    except RoutingConfigurationPersistenceError:
        return False
    return True


def _approvals_readable() -> bool:
    try:
        ExecutionRecordRow.objects.values_list("record_id", flat=True).first()
    except DatabaseError:
        return False
    return True


def _monitoring_readable() -> bool:
    try:
        MonitoringRecordRow.objects.values_list("id", flat=True).first()
    except DatabaseError:
        return False
    return True


def persistence_readiness() -> PersistenceReadiness:
    """Inspect authoritative persistence and critical read paths without mutations."""

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

    routing = "ready" if _routing_readable() else "unavailable"
    approvals = "ready" if _approvals_readable() else "unavailable"
    monitoring = "ready" if _monitoring_readable() else "unavailable"

    if routing != "ready":
        code = PersistenceReadinessCode.ROUTING_UNAVAILABLE
    elif approvals != "ready":
        code = PersistenceReadinessCode.APPROVALS_UNAVAILABLE
    elif monitoring != "ready":
        code = PersistenceReadinessCode.MONITORING_UNAVAILABLE
    else:
        code = PersistenceReadinessCode.READY

    return PersistenceReadiness(
        code,
        routing=routing,
        approvals=approvals,
        monitoring=monitoring,
    )
