from __future__ import annotations

import pytest

from qbet.layers import OperationalRiskLayer
from qbet.orchestrator import LiquidityChecker
from qbet.provider_accounts import (
    ProviderAccountSnapshot,
    ProviderAccountSource,
    ProviderAccountStatus,
)


class StubProviderAccountQuery:
    def __init__(self, snapshot: ProviderAccountSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[int, str]] = []

    def get_for_user(self, *, user_id: int, provider_id: str) -> ProviderAccountSnapshot:
        self.calls.append((user_id, provider_id))
        return self.snapshot

    def list_for_user(self, *, user_id: int) -> tuple[ProviderAccountSnapshot, ...]:
        return (self.snapshot,)


def account_snapshot() -> ProviderAccountSnapshot:
    return ProviderAccountSnapshot(
        provider_id="licensed-book",
        provider_name="Licensed Book",
        status=ProviderAccountStatus.ACTIVE,
        source=ProviderAccountSource.MANUAL,
        provider_eligible=True,
    )


def test_domain_risk_consumes_provider_accounts_through_typed_query() -> None:
    query = StubProviderAccountQuery(account_snapshot())
    risk = OperationalRiskLayer(provider_account_query=query)

    snapshot = risk.provider_account_snapshot(user_id=7, provider_id="licensed-book")

    assert snapshot.provider_id == "licensed-book"
    assert snapshot.is_declared_available
    assert not snapshot.is_verified
    assert query.calls == [(7, "licensed-book")]


def test_liquidity_checker_consumes_provider_accounts_through_same_typed_query() -> None:
    query = StubProviderAccountQuery(account_snapshot())
    checker = LiquidityChecker(provider_account_query=query)

    snapshot = checker.provider_account_snapshot(user_id=11, provider_id="licensed-book")

    assert snapshot.status is ProviderAccountStatus.ACTIVE
    assert snapshot.source is ProviderAccountSource.MANUAL
    assert query.calls == [(11, "licensed-book")]


def test_provider_account_consumers_fail_explicitly_without_query() -> None:
    with pytest.raises(ValueError, match="provider account query is not configured"):
        OperationalRiskLayer().provider_account_snapshot(
            user_id=1,
            provider_id="licensed-book",
        )
    with pytest.raises(ValueError, match="provider account query is not configured"):
        LiquidityChecker().provider_account_snapshot(
            user_id=1,
            provider_id="licensed-book",
        )
