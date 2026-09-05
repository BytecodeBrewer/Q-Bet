from decimal import Decimal

import pytest
from pydantic import ValidationError

from qbet.domain.ledger import LedgerCommand, LedgerOperation, PortfolioBalance
from qbet.ledger import PortfolioLedger


def command(operation, amount="10", **overrides):
    return LedgerCommand.model_validate({
        "id": operation.value,
        "dispatch_id": "dispatch",
        "correlation_id": "correlation",
        "currency": "EUR",
        "operation": operation,
        "amount": amount,
        **overrides,
    })


def ledger():
    return PortfolioLedger(
        balance=PortfolioBalance(mode="execution", currency="EUR", available=Decimal("100"))
    )


def test_full_lifecycle_and_replay():
    state = ledger()
    for operation in (LedgerOperation.RESERVE, LedgerOperation.LOCK, LedgerOperation.PENDING):
        state, decision = state.apply(command(operation))
        assert decision.accepted
    state, decision = state.apply(command(LedgerOperation.SETTLE, "12"))
    assert decision.accepted
    assert state.balance.available == 102
    assert state.balance.pending == 0
    assert state.balance.settled == 12
    repeated, decision = state.apply(command(LedgerOperation.SETTLE, "12"))
    assert repeated == state
    assert decision.duplicate
    unchanged, decision = state.apply(command(LedgerOperation.SETTLE, "13"))
    assert unchanged == state
    assert decision.reason == "idempotency_conflict"


@pytest.mark.parametrize("amount", ["-1", "Infinity", "-Infinity", "NaN"])
def test_non_finite_or_negative_capital_is_rejected(amount):
    with pytest.raises(ValidationError):
        PortfolioBalance.model_validate({"mode": "execution", "currency": "EUR", "available": amount})
    with pytest.raises(ValidationError):
        command(LedgerOperation.RESERVE, amount)


@pytest.mark.parametrize("operation", list(LedgerOperation))
def test_currency_is_checked_for_every_operation(operation):
    state = ledger()
    unchanged, decision = state.apply(command(operation, currency="GBP"))
    assert unchanged == state
    assert decision.reason == "currency_mismatch"


def test_insufficient_funds_and_unknown_dispatch_do_not_change_balance():
    state = ledger()
    unchanged, decision = state.apply(command(LedgerOperation.RESERVE, "101"))
    assert unchanged == state
    assert not decision.accepted
    unchanged, decision = state.apply(command(LedgerOperation.SETTLE))
    assert unchanged == state
    assert decision.reason == "unknown_dispatch"


def test_release_and_cost():
    state, _ = ledger().apply(command(LedgerOperation.RESERVE))
    state, decision = state.apply(command(LedgerOperation.RELEASE))
    assert decision.accepted
    assert state.balance.available == 100
    state, decision = state.apply(command(LedgerOperation.COST, "2"))
    assert decision.accepted
    assert state.balance.available == 98
    assert state.balance.cost == 2


def test_explicit_operations_use_the_same_idempotent_boundary():
    state, decision = ledger().reserve(
        command_id="reserve",
        dispatch_id="dispatch",
        correlation_id="correlation",
        amount=Decimal("10"),
    )
    assert decision.accepted
    state, decision = state.lock(
        command_id="lock",
        dispatch_id="dispatch",
        correlation_id="correlation",
        amount=Decimal("10"),
    )
    assert decision.accepted
    state, decision = state.mark_pending(
        command_id="pending",
        dispatch_id="dispatch",
        correlation_id="correlation",
        amount=Decimal("10"),
    )
    assert decision.accepted
    state, decision = state.settle_success(
        command_id="settle",
        dispatch_id="dispatch",
        correlation_id="correlation",
        amount=Decimal("11"),
    )
    assert decision.accepted
    assert state.balance.available == 101


def test_failed_dispatch_returns_only_reserved_principal():
    state, _ = ledger().apply(command(LedgerOperation.RESERVE))
    state, _ = state.apply(command(LedgerOperation.LOCK))
    unchanged, decision = state.apply(command(LedgerOperation.FAIL, "11"))
    assert unchanged == state
    assert not decision.accepted
    state, decision = state.apply(command(LedgerOperation.FAIL))
    assert decision.accepted
    assert state.balance.available == 100
    assert state.balance.locked == 0
