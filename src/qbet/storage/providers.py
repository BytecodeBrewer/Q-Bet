"""PostgreSQL repository for canonical sportsbook identity metadata."""

from __future__ import annotations

from django.db import DatabaseError, transaction

from qbet.providers import (
    ExternalProviderMapping,
    SportsbookCatalog,
    SportsbookProvider,
)
from qbet.storage.models import (
    SportsbookExternalIdentityRow,
    SportsbookProviderDomainRow,
    SportsbookProviderRow,
)


class PostgresSportsbookCatalogRepository:
    """Load and atomically replace the durable sportsbook catalog."""

    def load(self) -> SportsbookCatalog:
        try:
            providers = tuple(
                SportsbookProvider(
                    provider_id=row.provider_id,
                    legal_name=row.legal_name,
                    display_name=row.display_name,
                    domains=tuple(
                        row.domains.order_by("domain").values_list("domain", flat=True)
                    ),
                    jurisdiction=row.jurisdiction,
                    sports_betting=row.sports_betting,
                    online=row.online,
                    source_url=row.source_url,
                    whitelist_snapshot_date=row.whitelist_snapshot_date,
                    status=row.status,
                )
                for row in SportsbookProviderRow.objects.order_by("provider_id")
            )
            mappings = tuple(
                ExternalProviderMapping(
                    source_id=row.source_id,
                    external_key=row.external_key,
                    provider_id=row.provider_id,
                )
                for row in SportsbookExternalIdentityRow.objects.order_by(
                    "source_id", "external_key"
                )
            )
        except DatabaseError as error:
            raise OSError("sportsbook provider catalog is unavailable") from error
        return SportsbookCatalog(providers=providers, mappings=mappings)

    def replace(self, catalog: SportsbookCatalog) -> None:
        try:
            with transaction.atomic():
                SportsbookExternalIdentityRow.objects.all().delete()
                SportsbookProviderDomainRow.objects.all().delete()
                SportsbookProviderRow.objects.all().delete()
                SportsbookProviderRow.objects.bulk_create(
                    [
                        SportsbookProviderRow(
                            provider_id=provider.provider_id,
                            legal_name=provider.legal_name,
                            display_name=provider.display_name,
                            jurisdiction=provider.jurisdiction,
                            sports_betting=provider.sports_betting,
                            online=provider.online,
                            source_url=provider.source_url,
                            whitelist_snapshot_date=provider.whitelist_snapshot_date,
                            status=provider.status.value,
                        )
                        for provider in catalog.providers
                    ]
                )
                SportsbookProviderDomainRow.objects.bulk_create(
                    [
                        SportsbookProviderDomainRow(
                            domain=domain,
                            provider_id=provider.provider_id,
                        )
                        for provider in catalog.providers
                        for domain in provider.domains
                    ]
                )
                SportsbookExternalIdentityRow.objects.bulk_create(
                    [
                        SportsbookExternalIdentityRow(
                            source_id=mapping.source_id,
                            external_key=mapping.external_key,
                            provider_id=mapping.provider_id,
                        )
                        for mapping in catalog.mappings
                    ]
                )
        except DatabaseError as error:
            raise OSError("sportsbook provider catalog is unavailable") from error
