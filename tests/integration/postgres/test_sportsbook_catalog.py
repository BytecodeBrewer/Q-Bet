from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase

from qbet.providers import load_german_sportsbook_catalog
from qbet.storage.models import ProviderStateRow, SportsbookProviderRow
from qbet.storage.providers import PostgresSportsbookCatalogRepository


class SportsbookCatalogRepositoryTests(TestCase):
    def test_seeded_catalog_is_durable_and_separate_from_operational_provider_state(self) -> None:
        catalog = PostgresSportsbookCatalogRepository().load()

        self.assertEqual(catalog, load_german_sportsbook_catalog())
        self.assertEqual(SportsbookProviderRow.objects.count(), 24)
        self.assertEqual(ProviderStateRow.objects.count(), 0)

    def test_catalog_survives_repository_recreation(self) -> None:
        expected = load_german_sportsbook_catalog()
        PostgresSportsbookCatalogRepository().replace(expected)

        restored = PostgresSportsbookCatalogRepository().load()

        self.assertEqual(restored, expected)
        self.assertTrue(
            restored.resolve(
                source_id="the_odds_api",
                external_key="tipico_de",
            ).eligible
        )

    def test_catalog_hardening_migration_executes_on_postgresql(self) -> None:
        if connection.vendor != "postgresql":
            self.skipTest("catalog RLS migration is PostgreSQL-specific")

        migration = import_module(
            "qbet.storage.migrations.0013_sportsbook_provider_catalog"
        )
        with connection.schema_editor() as schema_editor:
            migration.harden_catalog_tables(apps, schema_editor)
