"""Persistent Bonus Offer dependency validation for stale-work fail-closed checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from django.apps import apps

from qbet.engines import BonusEngineRequest


class BonusDependencyState(StrEnum):
    UNBOUND = "unbound"
    CURRENT = "current"
    STALE = "stale"
    REMOVED = "removed"
    MISSING = "missing"


@dataclass(frozen=True)
class BonusDependencyCheck:
    state: BonusDependencyState
    reason_code: str | None = None

    @property
    def is_current(self) -> bool:
        return self.state in {BonusDependencyState.UNBOUND, BonusDependencyState.CURRENT}


class PostgresBonusOfferDependencyValidator:
    """Compare one typed BonusEngine dependency with authoritative BonusOffer state."""

    def check(self, request: BonusEngineRequest) -> BonusDependencyCheck:
        dependency = request.bonus_offer_dependency
        if dependency is None:
            return BonusDependencyCheck(BonusDependencyState.UNBOUND)

        BonusOffer = apps.get_model("web", "BonusOffer")
        row = (
            BonusOffer.objects.filter(
                pk=dependency.offer_id,
                user_id=dependency.owner_id,
            )
            .values("version", "retired_at")
            .first()
        )
        if row is None:
            return BonusDependencyCheck(
                BonusDependencyState.MISSING,
                "bonus_offer_dependency_missing",
            )
        if row["retired_at"] is not None:
            return BonusDependencyCheck(
                BonusDependencyState.REMOVED,
                "bonus_offer_dependency_removed",
            )
        if int(row["version"]) != dependency.offer_version:
            return BonusDependencyCheck(
                BonusDependencyState.STALE,
                "bonus_offer_dependency_stale",
            )
        return BonusDependencyCheck(BonusDependencyState.CURRENT)
