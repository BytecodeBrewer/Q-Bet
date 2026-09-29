"""Typed provider-account availability contract shared by product boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class ProviderAccountStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    DECLARED = "declared"
    ACTIVE = "active"
    UNAVAILABLE = "unavailable"
    FROZEN = "frozen"
    NEEDS_VERIFICATION = "needs_verification"


class ProviderAccountSource(StrEnum):
    MANUAL = "manual"
    API = "api"
    BROWSER = "browser"


@dataclass(frozen=True)
class ProviderAccountSnapshot:
    provider_id: str
    provider_name: str
    status: ProviderAccountStatus
    source: ProviderAccountSource | None = None
    nickname: str = ""
    notes: str = ""
    configured_at: datetime | None = None
    last_verified_at: datetime | None = None
    provider_eligible: bool = True

    @property
    def is_configured(self) -> bool:
        return self.status is not ProviderAccountStatus.NOT_CONFIGURED

    @property
    def is_declared_available(self) -> bool:
        """User-facing availability without pretending manual state is verified."""

        return self.provider_eligible and self.status is ProviderAccountStatus.ACTIVE

    @property
    def is_verified(self) -> bool:
        return (
            self.source in {ProviderAccountSource.API, ProviderAccountSource.BROWSER}
            and self.last_verified_at is not None
        )


class ProviderAccountQuery(Protocol):
    """Read boundary suitable for Domain Risk, Liquidity and funding planning."""

    def get_for_user(self, *, user_id: int, provider_id: str) -> ProviderAccountSnapshot:
        ...

    def list_for_user(self, *, user_id: int) -> tuple[ProviderAccountSnapshot, ...]:
        ...
