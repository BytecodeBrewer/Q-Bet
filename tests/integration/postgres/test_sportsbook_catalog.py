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
