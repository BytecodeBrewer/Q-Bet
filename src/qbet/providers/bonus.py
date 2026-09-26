"""German BonusEngine sportsbook preparation through canonical provider eligibility."""

from __future__ import annotations

from qbet.data.models import DataTarget, NormalizedMarketSnapshot
from qbet.providers import ProviderEligibilityReason, SportsbookCatalog


class BonusProviderEligibilityError(ValueError):
    def __init__(self, reason: ProviderEligibilityReason, external_identity: str) -> None:
        self.reason = reason
        self.external_identity = external_identity
        super().__init__(f"bonus sportsbook provider is not eligible: {reason.value}")


def canonicalize_german_bonus_snapshot(
    snapshot: NormalizedMarketSnapshot,
    catalog: SportsbookCatalog,
) -> NormalizedMarketSnapshot:
    """Resolve raw bookmaker identities before German BonusEngine preparation."""

    if snapshot.target is not DataTarget.BONUS:
        raise ValueError("German Bonus provider eligibility requires bonus target")
    canonical_offers = []
    for offer in snapshot.offers:
        resolution = catalog.resolve(
            source_id=snapshot.source.provider_id,
            external_key=offer.provider,
        )
        if not resolution.eligible or resolution.provider is None:
            raise BonusProviderEligibilityError(resolution.reason, offer.provider)
        canonical_offers.append(
            offer.model_copy(update={"provider": resolution.provider.provider_id})
        )
    return snapshot.model_copy(update={"offers": tuple(canonical_offers)})
