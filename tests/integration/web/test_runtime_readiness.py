from unittest.mock import patch

from django.db.migrations.exceptions import InconsistentMigrationHistory
from django.db.utils import OperationalError
from django.test import TestCase

from qbet.storage.ledger import RoutingConfigurationPersistenceError
from qbet.web.readiness import (
    PersistenceReadiness,
    PersistenceReadinessCode,
    deployment_release_id,
    persistence_readiness,
)

RELEASE_SHA = "1234567890abcdef1234567890abcdef12345678"


class RuntimePersistenceReadinessTests(TestCase):
    def test_current_migrated_postgres_is_ready(self) -> None:
        snapshot = persistence_readiness()

        self.assertTrue(snapshot.ready)
        self.assertEqual(snapshot.code, PersistenceReadinessCode.READY)
        self.assertEqual(
            snapshot.runtime,
            {
                "routing": "ready",
                "approvals": "ready",
                "monitoring": "ready",
            },
        )

    def test_database_failure_is_reported_without_sensitive_detail(self) -> None:
        with (
            patch(
                "qbet.web.readiness.connection.ensure_connection",
                side_effect=OperationalError(
                    "postgresql://secret-user:secret-pass@db.example/qbet"
                ),
            ),
            patch.dict("os.environ", {"QBET_RELEASE_SHA": RELEASE_SHA}),
        ):
            snapshot = persistence_readiness()
            response = self.client.get("/health/")

        self.assertFalse(snapshot.ready)
        self.assertEqual(snapshot.code, PersistenceReadinessCode.DATABASE_UNAVAILABLE)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "status": "unavailable",
                "service": "q-bet-web",
                "persistence": "database_unavailable",
                "readiness": "database_unavailable",
                "runtime": {
                    "routing": "unverified",
                    "approvals": "unverified",
                    "monitoring": "unverified",
                },
                "release": RELEASE_SHA,
            },
        )
        body = response.content.decode()
        self.assertNotIn("secret-user", body)
        self.assertNotIn("secret-pass", body)
        self.assertNotIn("db.example", body)

    def test_pending_migration_makes_health_unavailable(self) -> None:
        with patch("qbet.web.readiness.MigrationExecutor") as executor_factory:
            executor = executor_factory.return_value
            executor.loader.graph.leaf_nodes.return_value = [
                ("web", "0003_customer_report_access")
            ]
            executor.migration_plan.return_value = [object()]

            snapshot = persistence_readiness()
            response = self.client.get("/health/")

        self.assertFalse(snapshot.ready)
        self.assertEqual(snapshot.code, PersistenceReadinessCode.MIGRATIONS_PENDING)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["persistence"], "migrations_pending")
        self.assertEqual(response.json()["readiness"], "migrations_pending")
        self.assertNotIn("customer_report_access", response.content.decode())

    def test_invalid_migration_state_is_unavailable(self) -> None:
        with patch(
            "qbet.web.readiness.MigrationExecutor",
            side_effect=InconsistentMigrationHistory("invalid migration state"),
        ):
            snapshot = persistence_readiness()
            response = self.client.get("/health/")

        self.assertFalse(snapshot.ready)
        self.assertEqual(snapshot.code, PersistenceReadinessCode.MIGRATION_STATE_INVALID)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["persistence"], "migration_state_invalid")

    def test_routing_read_failure_keeps_persistence_ready_but_runtime_unavailable(self) -> None:
        with patch(
            "qbet.web.readiness.RoutingConfigurationRepository.load",
            side_effect=RoutingConfigurationPersistenceError("routing unavailable"),
        ):
            snapshot = persistence_readiness()
            response = self.client.get("/health/")

        self.assertFalse(snapshot.ready)
        self.assertEqual(snapshot.code, PersistenceReadinessCode.ROUTING_UNAVAILABLE)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["persistence"], "ready")
        self.assertEqual(response.json()["readiness"], "routing_unavailable")
        self.assertEqual(response.json()["runtime"]["routing"], "unavailable")

    def test_approval_read_failure_is_visible_without_mutating_approval_state(self) -> None:
        with patch(
            "qbet.web.readiness.ExecutionRecordRow.objects.values_list",
            side_effect=OperationalError("approval table unavailable"),
        ):
            snapshot = persistence_readiness()
            response = self.client.get("/health/")

        self.assertFalse(snapshot.ready)
        self.assertEqual(snapshot.code, PersistenceReadinessCode.APPROVALS_UNAVAILABLE)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["persistence"], "ready")
        self.assertEqual(response.json()["runtime"]["approvals"], "unavailable")

    def test_monitoring_read_failure_is_visible(self) -> None:
        with patch(
            "qbet.web.readiness.MonitoringRecordRow.objects.values_list",
            side_effect=OperationalError("monitoring table unavailable"),
        ):
            snapshot = persistence_readiness()
            response = self.client.get("/health/")

        self.assertFalse(snapshot.ready)
        self.assertEqual(snapshot.code, PersistenceReadinessCode.MONITORING_UNAVAILABLE)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["persistence"], "ready")
        self.assertEqual(response.json()["runtime"]["monitoring"], "unavailable")

    def test_health_is_ok_only_when_all_runtime_reads_are_ready(self) -> None:
        with (
            patch(
                "qbet.web.views.persistence_readiness",
                return_value=PersistenceReadiness(
                    PersistenceReadinessCode.READY,
                    routing="ready",
                    approvals="ready",
                    monitoring="ready",
                ),
            ),
            patch(
                "qbet.web.views.deployment_release_id",
                return_value=RELEASE_SHA,
            ),
        ):
            response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "q-bet-web",
                "persistence": "ready",
                "readiness": "ready",
                "runtime": {
                    "routing": "ready",
                    "approvals": "ready",
                    "monitoring": "ready",
                },
                "release": RELEASE_SHA,
            },
        )

    def test_release_id_prefers_explicit_reviewed_sha(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "QBET_RELEASE_SHA": RELEASE_SHA,
                "VERCEL_GIT_COMMIT_SHA": "abcdefabcdefabcdefabcdefabcdefabcdefabcd",
            },
            clear=False,
        ):
            self.assertEqual(deployment_release_id(), RELEASE_SHA)

    def test_release_id_does_not_expose_non_sha_environment_values(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "QBET_RELEASE_SHA": "not-a-sha-or-secret",
                "VERCEL_GIT_COMMIT_SHA": "",
                "GITHUB_SHA": "",
            },
            clear=False,
        ):
            self.assertEqual(deployment_release_id(), "unknown")
