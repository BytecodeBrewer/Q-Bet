from __future__ import annotations

from qbet.web.bonus_offer_policy import (
    BonusCoveragePolicy,
    evaluate_bonus_coverage,
)


POLICY = BonusCoveragePolicy(
    compare_offer_count=5,
    compare_provider_count=2,
    good_offer_count=10,
    good_provider_count=3,
    healthy_offer_count=15,
    healthy_provider_count=4,
)


def test_bonus_coverage_progresses_without_becoming_an_execution_gate() -> None:
    cases = (
        (0, 0, "needs_data"),
        (1, 1, "limited"),
        (5, 2, "growing"),
        (10, 3, "good"),
        (15, 4, "healthy"),
    )

    for offers, providers, expected in cases:
        health = evaluate_bonus_coverage(
            usable_offer_count=offers,
            provider_count=providers,
            policy=POLICY,
        )
        assert health.state == expected
        assert health.usable_offer_count == offers
        assert health.provider_count == providers


def test_provider_diversity_prevents_offer_count_alone_from_claiming_health() -> None:
    health = evaluate_bonus_coverage(
        usable_offer_count=20,
        provider_count=1,
        policy=POLICY,
    )

    assert health.state == "limited"
