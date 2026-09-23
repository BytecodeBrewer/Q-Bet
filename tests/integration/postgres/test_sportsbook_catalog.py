from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase

from qbet.providers import SportsbookCatalog, load_german_sportsbook_catalog
from qbet.storage.models import ProviderStateRow, SportsbookProviderRow
from qbet.storage.providers import PostgresSportsbookCatalogRepository


def assert_catalog_equal(actual: SportsbookCatalog, expected: SportsbookCatalog) -> None:
    actual_providers = {
        provider.provider_id: provider.model_dump(mode="json") for provider in actual.providers
    }
    expected_providers = {
        provider.provider_id: provider.model_dump(mode="json") for provider in expected.providers
    }
    assert actual_providers == expected_providers
    assert {
        (mapping.source_id, mapping.external_key, mapping.provider_id)
        for mapping in actual.mappings
    } == {
        (mapping.source_id, mapping.external_key, mapping.provider_id)
        for mapping in expected.mappings
    }


class SportsbookCatalogRepositoryTests(TestCase):
    def test_seed_migration_populates_durable_catalog_separate_from_provider_state(self) -> None:
        migration = import_module(
            "qbet.storage.migrations.0013_sportsbook_provider_catalog"
        )
        migration.seed_catalog(apps, None)

        catalog = PostgresSportsbookCatalogRepository().load()
        expected = load_german_sportsbook_catalog()

        assert_catalog_equal(catalog, expected)
        self.assertEqual(SportsbookProviderRow.objects.count(), 24)
        self.assertEqual(ProviderStateRow.objects.count(), 0)

    def test_catalog_survives_repository_recreation(self) -> None:
        expected = load_german_sportsbook_catalog()
        PostgresSportsbookCatalogRepository().replace(expected)

        restored = PostgresSportsbookCatalogRepository().load()

        assert_catalog_equal(restored, expected)
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
