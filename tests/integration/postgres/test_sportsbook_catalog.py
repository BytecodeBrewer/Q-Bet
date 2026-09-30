from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from qbet.provider_accounts import ProviderAccountStatus
from qbet.providers import SportsbookCatalog, load_german_sportsbook_catalog
from qbet.storage.models import ProviderStateRow, SportsbookProviderRow
from qbet.storage.providers import PostgresSportsbookCatalogRepository
from qbet.web.models import ProviderAccount


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
        self.assertEqual(SportsbookProviderRow.objects.count(), 26)
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

    def test_catalog_refresh_updates_provider_in_place_when_user_account_references_it(self) -> None:
        repository = PostgresSportsbookCatalogRepository()
        original = load_german_sportsbook_catalog()
        repository.replace(original)
        provider = original.providers[0]
        provider_row = SportsbookProviderRow.objects.get(provider_id=provider.provider_id)
        user = get_user_model().objects.create_user(username="catalog-refresh-user")
        account = ProviderAccount.objects.create(
            user=user,
            provider=provider_row,
            status=ProviderAccountStatus.ACTIVE.value,
        )
        updated_provider = provider.model_copy(
            update={"display_name": f"{provider.display_name} Updated"}
        )
        updated = original.model_copy(
            update={
                "providers": tuple(
                    updated_provider
                    if item.provider_id == provider.provider_id
                    else item
                    for item in original.providers
                )
            }
        )

        repository.replace(updated)

        account.refresh_from_db()
        provider_row.refresh_from_db()
        self.assertEqual(account.provider_id, provider.provider_id)
        self.assertEqual(provider_row.display_name, updated_provider.display_name)
        assert_catalog_equal(repository.load(), updated)

    def test_catalog_refresh_fails_closed_when_removed_provider_is_still_referenced(self) -> None:
        repository = PostgresSportsbookCatalogRepository()
        original = load_german_sportsbook_catalog()
        repository.replace(original)
        provider = original.providers[0]
        provider_row = SportsbookProviderRow.objects.get(provider_id=provider.provider_id)
        user = get_user_model().objects.create_user(username="catalog-remove-user")
        ProviderAccount.objects.create(
            user=user,
            provider=provider_row,
            status=ProviderAccountStatus.ACTIVE.value,
        )
        reduced = original.model_copy(
            update={
                "providers": tuple(
                    item
                    for item in original.providers
                    if item.provider_id != provider.provider_id
                ),
                "mappings": tuple(
                    mapping
                    for mapping in original.mappings
                    if mapping.provider_id != provider.provider_id
                ),
            }
        )

        with self.assertRaisesRegex(
            OSError,
            "cannot remove referenced providers",
        ):
            repository.replace(reduced)

        self.assertTrue(
            ProviderAccount.objects.filter(
                user=user,
                provider_id=provider.provider_id,
            ).exists()
        )
        assert_catalog_equal(repository.load(), original)

    def test_catalog_hardening_migration_executes_on_postgresql(self) -> None:
        if connection.vendor != "postgresql":
            self.skipTest("catalog RLS migration is PostgreSQL-specific")

        migration = import_module(
            "qbet.storage.migrations.0013_sportsbook_provider_catalog"
        )
        with connection.schema_editor() as schema_editor:
            migration.harden_catalog_tables(apps, schema_editor)
