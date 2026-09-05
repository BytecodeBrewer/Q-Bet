"""Deterministic ledger transitions, persisted by the storage boundary."""

from decimal import Decimal

from pydantic import Field

from qbet.domain.ledger import (
    LedgerCommand, LedgerDecision, LedgerOperation, PortfolioBalance,
)
from qbet.domain.models import DomainModel


class Position(DomainModel):
    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    state: str


class PortfolioLedger(DomainModel):
    balance: PortfolioBalance
    positions: dict[str, Position] = Field(default_factory=dict)
    commands: dict[str, LedgerCommand] = Field(default_factory=dict)

    def apply(self, command: LedgerCommand) -> tuple["PortfolioLedger", LedgerDecision]:
        def reject(reason: str) -> tuple["PortfolioLedger", LedgerDecision]:
            return self, LedgerDecision(accepted=False, balance=self.balance, reason=reason)

        previous = self.commands.get(command.id)
        if previous is not None:
            if previous != command:
                return reject("idempotency_conflict")
            return self, LedgerDecision(accepted=True, balance=self.balance, duplicate=True)
        if command.currency != self.balance.currency:
            return reject("currency_mismatch")
        balances = self.balance.model_dump()
        positions = dict(self.positions)
        position = positions.get(command.dispatch_id)
        operation = command.operation
        amount = command.amount
        if operation is LedgerOperation.RESERVE:
            if position is not None:
                return reject("dispatch_already_exists")
            if amount <= 0 or amount > self.balance.available:
                return reject("insufficient_available_capital")
            balances["available"] -= amount
            balances["reserved"] += amount
            positions[command.dispatch_id] = Position(amount=amount, state="reserved")
        elif operation is LedgerOperation.COST:
            if amount > self.balance.available:
                return reject("insufficient_available_capital")
            balances["available"] -= amount
            balances["cost"] += amount
        else:
            if position is None:
                return reject("unknown_dispatch")
            source = position.state
            expected = {
                LedgerOperation.RELEASE: {"reserved"},
                LedgerOperation.LOCK: {"reserved"},
                LedgerOperation.PENDING: {"locked"},
                LedgerOperation.SETTLE: {"pending"},
                LedgerOperation.FAIL: {"reserved", "locked", "pending"},
            }
            if source not in expected.get(operation, set()):
                return reject("invalid_position_state")
            if operation is not LedgerOperation.SETTLE and amount != position.amount:
                return reject("position_amount_mismatch")
            balances[source] -= position.amount
            if operation is LedgerOperation.LOCK:
                target = "locked"
                balances[target] += amount
            elif operation is LedgerOperation.PENDING:
                target = "pending"
                balances[target] += amount
            elif operation is LedgerOperation.SETTLE:
                target = "settled"
                balances["available"] += amount
                balances["settled"] += amount
            else:
                target = "failed" if operation is LedgerOperation.FAIL else "released"
                balances["available"] += amount
            positions[command.dispatch_id] = Position(amount=amount, state=target)
        ledger = PortfolioLedger(
            balance=PortfolioBalance.model_validate(balances),
            positions=positions,
            commands={**self.commands, command.id: command},
        )
        return ledger, LedgerDecision(accepted=True, balance=ledger.balance)
