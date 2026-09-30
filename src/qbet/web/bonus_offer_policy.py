"""Bonus Offer product policy: promotion coverage, duplicate detection and audit-safe helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import re
from typing import Literal

from django.conf import settings

from qbet.web.models import BonusOffer


CoverageState = Literal["needs_data", "limited", "growing", "good", "healthy"]
DuplicateKind = Literal["exact", "near"]


@dataclass(frozen=True)
class BonusCoveragePolicy:
    """Configurable product thresholds; calculation eligibility remains independent."""

    compare_offer_count: int = 5
    compare_provider_count: int = 2
    good_offer_count: int = 10
    good_provider_count: int = 3
    healthy_offer_count: int = 15
    healthy_provider_count: int = 4


@dataclass(frozen=True)
class BonusCoverageHealth:
    state: CoverageState
    usable_offer_count: int
    provider_count: int


@dataclass(frozen=True)
class BonusOfferGuidance:
    title: str
    detail: str


@dataclass(frozen=True)
class BonusOfferCandidate:
    provider_id: str
    promotion_shape: str
    promotion_value: Decimal | None
    currency: str
    required_stake: Decimal | None
    minimum_odds: Decimal | None
    wagering_requirement: Decimal | None
    stake_return_rule: str
    valid_until: datetime
    name: str
    unsupported_terms: str = ""


@dataclass(frozen=True)
class BonusDuplicateMatch:
    kind: DuplicateKind
    offer: BonusOffer


def current_bonus_coverage_policy() -> BonusCoveragePolicy:
    """Read thresholds from Django settings without coupling presentation copy to them."""

    return BonusCoveragePolicy(
        compare_offer_count=int(getattr(settings, "QBET_BONUS_COVERAGE_COMPARE_OFFERS", 5)),
        compare_provider_count=int(
            getattr(settings, "QBET_BONUS_COVERAGE_COMPARE_PROVIDERS", 2)
        ),
        good_offer_count=int(getattr(settings, "QBET_BONUS_COVERAGE_GOOD_OFFERS", 10)),
        good_provider_count=int(getattr(settings, "QBET_BONUS_COVERAGE_GOOD_PROVIDERS", 3)),
        healthy_offer_count=int(
            getattr(settings, "QBET_BONUS_COVERAGE_HEALTHY_OFFERS", 15)
        ),
        healthy_provider_count=int(
            getattr(settings, "QBET_BONUS_COVERAGE_HEALTHY_PROVIDERS", 4)
        ),
    )


def evaluate_bonus_coverage(
    *,
    usable_offer_count: int,
    provider_count: int,
    policy: BonusCoveragePolicy | None = None,
) -> BonusCoverageHealth:
    """Classify coverage only; it never disables a mathematically usable offer."""

    selected = policy or current_bonus_coverage_policy()
    if usable_offer_count <= 0:
        state: CoverageState = "needs_data"
    elif (
        usable_offer_count >= selected.healthy_offer_count
        and provider_count >= selected.healthy_provider_count
    ):
        state = "healthy"
    elif (
        usable_offer_count >= selected.good_offer_count
        and provider_count >= selected.good_provider_count
    ):
        state = "good"
    elif (
        usable_offer_count >= selected.compare_offer_count
        and provider_count >= selected.compare_provider_count
    ):
        state = "growing"
    else:
        state = "limited"
    return BonusCoverageHealth(
        state=state,
        usable_offer_count=usable_offer_count,
        provider_count=provider_count,
    )


def bonus_offer_guidance(
    health: BonusCoverageHealth,
) -> BonusOfferGuidance | None:
    """Translate internal readiness policy into natural customer-facing guidance."""

    offers = health.usable_offer_count
    providers = health.provider_count
    if health.state == "needs_data":
        return BonusOfferGuidance(
            title="No bonus offers to compare yet",
            detail=(
                "Add a sportsbook promotion when you want BonusEngine to compare it "
                "with current matches."
            ),
        )
    if health.state == "limited":
        return BonusOfferGuidance(
            title="Only a few bonus offers are available",
            detail=(
                f"{offers} bonus offer(s) from {providers} sportsbook(s) can currently "
                "be compared. More offers may improve the comparison."
            ),
        )
    if health.state == "growing":
        return BonusOfferGuidance(
            title="More bonus offers can improve comparisons",
            detail=(
                f"{offers} bonus offer(s) from {providers} sportsbook(s) can currently "
                "be compared."
            ),
        )
    return None


def find_bonus_offer_duplicate(
    *,
    user_id: int,
    candidate: BonusOfferCandidate,
    exclude_offer_id: int | None = None,
) -> BonusDuplicateMatch | None:
    """Return an exact duplicate first, otherwise one conservative near-duplicate.

    The seam accepts normalized business inputs rather than HTTP/form state so future
    ingestion adapters can reuse the same check.
    """

    offers = BonusOffer.objects.filter(
        user_id=user_id,
        retired_at__isnull=True,
    ).select_related("provider")
    if exclude_offer_id is not None:
        offers = offers.exclude(pk=exclude_offer_id)

    exact_signature = _candidate_signature(candidate)
    near_match: BonusOffer | None = None
    for offer in offers.order_by("-updated_at", "-id"):
        if _offer_signature(offer) == exact_signature:
            return BonusDuplicateMatch(kind="exact", offer=offer)
        if near_match is None and _is_near_duplicate(offer, candidate):
            near_match = offer

    if near_match is not None:
        return BonusDuplicateMatch(kind="near", offer=near_match)
    return None


def bonus_offer_snapshot(offer: BonusOffer) -> dict[str, object]:
    """Return JSON-safe business fields for edit audit history."""

    return {
        "provider_id": offer.provider.provider_id,
        "name": offer.name,
        "promotion_shape": offer.effective_promotion_shape,
        "promotion_type": offer.promotion_type,
        "promotion_value": _decimal_text(offer.promotion_value),
        "currency": offer.currency,
        "required_stake": _decimal_text(offer.required_stake),
        "minimum_odds": _decimal_text(offer.minimum_odds),
        "wagering_requirement": _decimal_text(offer.wagering_requirement),
        "stake_return_rule": offer.stake_return_rule,
        "unsupported_terms": offer.unsupported_terms,
        "valid_until": offer.valid_until.isoformat(),
        "notes": offer.notes,
        "version": offer.version,
        "retired_at": offer.retired_at.isoformat() if offer.retired_at is not None else None,
    }


def _candidate_signature(candidate: BonusOfferCandidate) -> tuple[object, ...]:
    return (
        candidate.provider_id,
        candidate.promotion_shape,
        _decimal_text(candidate.promotion_value),
        candidate.currency.upper(),
        _decimal_text(candidate.required_stake),
        _decimal_text(candidate.minimum_odds),
        _decimal_text(candidate.wagering_requirement),
        candidate.stake_return_rule,
        _minute(candidate.valid_until),
        _normalize_text(candidate.name),
        _normalize_text(candidate.unsupported_terms),
    )


def _offer_signature(offer: BonusOffer) -> tuple[object, ...]:
    return (
        offer.provider.provider_id,
        offer.effective_promotion_shape,
        _decimal_text(offer.promotion_value),
        offer.currency.upper(),
        _decimal_text(offer.required_stake),
        _decimal_text(offer.minimum_odds),
        _decimal_text(offer.wagering_requirement),
        offer.stake_return_rule,
        _minute(offer.valid_until),
        _normalize_text(offer.name),
        _normalize_text(offer.unsupported_terms),
    )


def _is_near_duplicate(offer: BonusOffer, candidate: BonusOfferCandidate) -> bool:
    if offer.provider.provider_id != candidate.provider_id:
        return False
    if offer.effective_promotion_shape != candidate.promotion_shape:
        return False
    if offer.currency.upper() != candidate.currency.upper():
        return False
    if abs(offer.valid_until - candidate.valid_until) > timedelta(days=3):
        return False

    title_matches = _normalize_text(offer.name) == _normalize_text(candidate.name)
    amount_matches = (
        _decimal_text(offer.promotion_value) == _decimal_text(candidate.promotion_value)
        and _decimal_text(offer.required_stake) == _decimal_text(candidate.required_stake)
    )
    return title_matches or amount_matches


def _minute(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")
