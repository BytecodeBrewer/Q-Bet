"""German licensed sportsbook provider catalog and fail-closed identity resolution."""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from importlib.resources import files
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from qbet.domain.models import DomainModel, Identifier

GGL_WHITELIST_URL = (
    "https://gluecksspiel-behoerde.de/de/fuer-spielende/"
    "uebersicht-erlaubter-anbieter-whitelist"
)
GERMAN_JURISDICTION = "DE"


class ProviderStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ProviderEligibilityReason(StrEnum):
    ELIGIBLE = "eligible"
    UNKNOWN_EXTERNAL_IDENTITY = "unknown_external_identity"
    AMBIGUOUS_EXTERNAL_IDENTITY = "ambiguous_external_identity"
    UNMAPPED_EXTERNAL_IDENTITY = "unmapped_external_identity"
    INELIGIBLE_PROVIDER = "ineligible_provider"


class SportsbookProvider(DomainModel):
    provider_id: Identifier
    legal_name: Identifier
    display_name: Identifier
    domains: tuple[Identifier, ...] = Field(min_length=1)
    jurisdiction: str
    sports_betting: bool
    online: bool
    source_url: Identifier
    whitelist_snapshot_date: date
    status: ProviderStatus = ProviderStatus.ACTIVE

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(_normalize_domain(item) for item in value))
        if len(set(normalized)) != len(normalized):
            raise ValueError("provider domains must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_german_sportsbook_status(self) -> SportsbookProvider:
        if self.jurisdiction != GERMAN_JURISDICTION:
            raise ValueError("sportsbook catalog currently supports jurisdiction DE only")
        if self.status is ProviderStatus.ACTIVE and (not self.sports_betting or not self.online):
            raise ValueError("active German sportsbook must be online sports-betting eligible")
        if self.source_url != GGL_WHITELIST_URL:
            raise ValueError("German sportsbook provenance must use the GGL whitelist")
        return self


class ExternalProviderMapping(DomainModel):
    source_id: Identifier
    external_key: Identifier
    provider_id: Identifier


class ProviderResolution(DomainModel):
    provider: SportsbookProvider | None = None
    reason: ProviderEligibilityReason

    @property
    def eligible(self) -> bool:
        return self.reason is ProviderEligibilityReason.ELIGIBLE and self.provider is not None


class SportsbookCatalog(DomainModel):
    providers: tuple[SportsbookProvider, ...]
    mappings: tuple[ExternalProviderMapping, ...] = ()

    @model_validator(mode="after")
    def validate_catalog(self) -> SportsbookCatalog:
        provider_ids = [provider.provider_id for provider in self.providers]
        if len(set(provider_ids)) != len(provider_ids):
            raise ValueError("duplicate canonical provider_id")

        domain_owners: dict[str, str] = {}
        for provider in self.providers:
            for domain in provider.domains:
                owner = domain_owners.setdefault(domain, provider.provider_id)
                if owner != provider.provider_id:
                    raise ValueError(f"duplicate domain ownership: {domain}")

        known = set(provider_ids)
        mapping_keys: set[tuple[str, str]] = set()
        for mapping in self.mappings:
            if mapping.provider_id not in known:
                raise ValueError("external mapping references unknown provider")
            key = (mapping.source_id, mapping.external_key.casefold())
            if key in mapping_keys:
                raise ValueError("duplicate external source/key mapping")
            mapping_keys.add(key)
        return self

    def resolve(
        self,
        *,
        source_id: str,
        external_key: str | None = None,
        domain: str | None = None,
    ) -> ProviderResolution:
        candidates: set[str] = set()
        if external_key:
            key = external_key.strip().casefold()
            candidates.update(
                mapping.provider_id
                for mapping in self.mappings
                if mapping.source_id == source_id and mapping.external_key.casefold() == key
            )
        if domain:
            normalized = _normalize_domain(domain)
            candidates.update(
                provider.provider_id
                for provider in self.providers
                if normalized in provider.domains
            )
        if not candidates:
            reason = (
                ProviderEligibilityReason.UNMAPPED_EXTERNAL_IDENTITY
                if external_key
                else ProviderEligibilityReason.UNKNOWN_EXTERNAL_IDENTITY
            )
            return ProviderResolution(reason=reason)
        if len(candidates) != 1:
            return ProviderResolution(reason=ProviderEligibilityReason.AMBIGUOUS_EXTERNAL_IDENTITY)
        provider_id = next(iter(candidates))
        provider = next(item for item in self.providers if item.provider_id == provider_id)
        if (
            provider.status is not ProviderStatus.ACTIVE
            or provider.jurisdiction != GERMAN_JURISDICTION
            or not provider.sports_betting
            or not provider.online
        ):
            return ProviderResolution(
                provider=provider,
                reason=ProviderEligibilityReason.INELIGIBLE_PROVIDER,
            )
        return ProviderResolution(provider=provider, reason=ProviderEligibilityReason.ELIGIBLE)

    def market_data_ready_provider_ids(self, source_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    mapping.provider_id
                    for mapping in self.mappings
                    if mapping.source_id == source_id
                }
            )
        )


def load_german_sportsbook_catalog() -> SportsbookCatalog:
    resource = files("qbet.providers").joinpath("ggl_sportsbooks_2026-09-07.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    return SportsbookCatalog.model_validate(payload)


def _normalize_domain(value: str) -> str:
    raw = value.strip().casefold()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = parsed.hostname
    if not host or parsed.username or parsed.password or parsed.port is not None:
        raise ValueError(f"malformed sportsbook domain: {value}")
    if "." not in host or any(not part for part in host.split(".")):
        raise ValueError(f"malformed sportsbook domain: {value}")
    path = parsed.path.strip("/")
    return f"{host}/{path}" if path else host
