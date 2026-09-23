from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from qbet.providers import (
    GGL_WHITELIST_URL,
    ExternalProviderMapping,
    ProviderEligibilityReason,
    SportsbookCatalog,
    SportsbookProvider,
    load_german_sportsbook_catalog,
)


def provider(**changes: object) -> SportsbookProvider:
    values: dict[str, object] = {
        "provider_id": "licensed-book",
        "legal_name": "Licensed Book GmbH",
        "display_name": "Licensed Book",
        "domains": ("licensed.example.de",),
        "jurisdiction": "DE",
        "sports_betting": True,
        "online": True,
        "source_url": GGL_WHITELIST_URL,
        "whitelist_snapshot_date": date(2026, 9, 7),
        "status": "active",
    }
    values.update(changes)
    return SportsbookProvider.model_validate(values)


def test_reviewed_ggl_snapshot_contains_every_reviewed_online_sportsbook() -> None:
    catalog = load_german_sportsbook_catalog()

    assert len(catalog.providers) == 25
    assert {item.whitelist_snapshot_date for item in catalog.providers} == {date(2026, 9, 7)}
    assert all(item.source_url == GGL_WHITELIST_URL for item in catalog.providers)
    assert all(item.jurisdiction == "DE" and item.online for item in catalog.providers)
    assert catalog.resolve(source_id="domain", domain="BET3000.DE").provider.provider_id == (
        "bet3000-wettarena"
    )
    assert catalog.resolve(
        source_id="domain", domain="wettarena.de"
    ).provider.provider_id == "bet3000-wettarena"


def test_verified_the_odds_api_mapping_resolves_only_german_identity() -> None:
    catalog = load_german_sportsbook_catalog()

    tipico = catalog.resolve(source_id="the_odds_api", external_key="tipico_de")
    winamax = catalog.resolve(source_id="the_odds_api", external_key="winamax_de")
    unmapped = catalog.resolve(source_id="the_odds_api", external_key="betfair_ex_eu")

    assert tipico.eligible and tipico.provider.provider_id == "tipico"
    assert winamax.eligible and winamax.provider.provider_id == "winamax"
    assert unmapped.reason is ProviderEligibilityReason.UNMAPPED_EXTERNAL_IDENTITY
    assert "betfair" not in {item.provider_id for item in catalog.providers}


def test_legal_but_unmapped_provider_remains_listable() -> None:
    catalog = load_german_sportsbook_catalog()
    provider_ids = {item.provider_id for item in catalog.providers}
    ready_provider_ids = set(catalog.market_data_ready_provider_ids("the_odds_api"))

    assert {"888sport", "bet365"} <= provider_ids
    assert {"888sport", "bet365"}.isdisjoint(ready_provider_ids)
    assert catalog.resolve(source_id="domain", domain="888SPORT.DE").provider.provider_id == (
        "888sport"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"domains": ("not-a-domain",)},
        {"jurisdiction": "GB"},
        {"sports_betting": False},
        {"online": False},
        {"source_url": "https://example.invalid/list"},
    ],
)
def test_provider_validation_rejects_invalid_seed_metadata(changes: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        provider(**changes)


def test_catalog_rejects_duplicate_ids_domains_and_invalid_mappings() -> None:
    first = provider()
    duplicate_id = provider(domains=("second.example.de",))
    with pytest.raises(ValidationError, match="duplicate canonical"):
        SportsbookCatalog(providers=(first, duplicate_id))

    second = provider(provider_id="other-book", legal_name="Other", domains=first.domains)
    with pytest.raises(ValidationError, match="duplicate domain"):
        SportsbookCatalog(providers=(first, second))

    with pytest.raises(ValidationError, match="unknown provider"):
        SportsbookCatalog(
            providers=(first,),
            mappings=(
                ExternalProviderMapping(
                    source_id="feed",
                    external_key="book",
                    provider_id="missing",
                ),
            ),
        )


def test_resolution_has_stable_ambiguous_and_ineligible_reason_codes() -> None:
    active = provider()
    inactive = provider(
        provider_id="inactive-book",
        legal_name="Inactive Book GmbH",
        display_name="Inactive",
        domains=("inactive.example.de",),
        sports_betting=False,
        online=False,
        status="inactive",
    )
    catalog = SportsbookCatalog(
        providers=(active, inactive),
        mappings=(
            ExternalProviderMapping(
                source_id="feed",
                external_key="licensed",
                provider_id=active.provider_id,
            ),
            ExternalProviderMapping(
                source_id="feed",
                external_key="inactive",
                provider_id=inactive.provider_id,
            ),
        ),
    )

    assert catalog.resolve(
        source_id="feed",
        external_key="licensed",
        domain="inactive.example.de",
    ).reason is ProviderEligibilityReason.AMBIGUOUS_EXTERNAL_IDENTITY
    assert catalog.resolve(
        source_id="feed",
        external_key="inactive",
    ).reason is ProviderEligibilityReason.INELIGIBLE_PROVIDER
    assert catalog.resolve(
        source_id="feed",
        domain="missing.example.de",
    ).reason is ProviderEligibilityReason.UNKNOWN_EXTERNAL_IDENTITY


def test_catalog_rejects_duplicate_source_key_mapping() -> None:
    item = provider()
    mapping = ExternalProviderMapping(
        source_id="feed",
        external_key="book",
        provider_id=item.provider_id,
    )
    with pytest.raises(ValidationError, match="duplicate external"):
        SportsbookCatalog(providers=(item,), mappings=(mapping, mapping))
