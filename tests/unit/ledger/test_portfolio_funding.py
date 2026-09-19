from decimal import Decimal

from qbet.domain.ledger import PortfolioBalance
from qbet.ledger import PortfolioLedger


def simulation_ledger() -> PortfolioLedger:
    return PortfolioLedger(
        balance=PortfolioBalance(
            mode="simulation",
            currency="EUR",
            available=Decimal("100"),
        )
    )


def test_external_funding_credit_is_idempotent_without_position() -> None:
    state = simulation_ledger()

    first, decision = state.fund_external(
        command_id="funding:p1:credit",
        dispatch_id="funding:p1",
        correlation_id="c1",
        amount=Decimal("25"),
    )
    repeated, duplicate = first.fund_external(
        command_id="funding:p1:credit",
        dispatch_id="funding:p1",
        correlation_id="c1",
        amount=Decimal("25"),
    )
    conflict, rejected = first.fund_external(
        command_id="funding:p1:credit",
        dispatch_id="funding:p1",
        correlation_id="c1",
        amount=Decimal("26"),
    )

    assert decision.accepted
    assert first.balance.available == Decimal("125")
    assert first.positions == {}
    assert duplicate.accepted and duplicate.duplicate
    assert repeated == first
    assert conflict == first
    assert rejected.reason == "idempotency_conflict"


def test_external_funding_rejects_zero_and_position_collision() -> None:
    state = simulation_ledger()
    unchanged, decision = state.fund_external(
        command_id="zero",
        dispatch_id="funding:zero",
        correlation_id="c1",
        amount=Decimal("0"),
    )
    reserved, reserve = state.reserve(
        command_id="reserve",
        dispatch_id="funding:p2",
        correlation_id="c1",
        amount=Decimal("10"),
    )
    collided, collision = reserved.fund_external(
        command_id="funding:p2:credit",
        dispatch_id="funding:p2",
        correlation_id="c1",
        amount=Decimal("5"),
    )

    assert unchanged == state
    assert decision.reason == "funding_amount_must_be_positive"
    assert reserve.accepted
    assert collided == reserved
    assert collision.reason == "funding_dispatch_conflict"
