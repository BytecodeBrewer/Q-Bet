from unittest.mock import patch

from django.db.utils import OperationalError
from django.test import TestCase

from qbet.web.readiness import (
    PersistenceReadiness,
    PersistenceReadinessCode,
    persistence_readiness,
)


class RuntimePersistenceReadinessTests(TestCase):
    def test_current_migrated_postgres_is_ready(self) -> None:
        snapshot = persistence_readiness()

        self.assertTrue(snapshot.ready)
        self.assertEqual(snapshot.code, PersistenceReadinessCode.READY)

    def test_database_failure_is_reported_without_sensitive_detail(self) -> None:
        with patch(
            "qbet.web.readiness.connection.ensure_connection",
            side_effect=OperationalError("postgresql://secret-user:secret-pass@db.example/qbet"),
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
        self.assertNotIn("customer_report_access", response.content.decode())

    def test_health_is_ok_only_when_persistence_is_ready(self) -> None:
        with patch(
            "qbet.web.views.persistence_readiness",
            return_value=PersistenceReadiness(PersistenceReadinessCode.READY),
        ):
            response = self.client.get("/health/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "q-bet-web",
                "persistence": "ready",
            },
        )
