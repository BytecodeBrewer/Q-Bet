"""Deterministic ledger transitions, persisted by the storage boundary."""

from decimal import Decimal

from pydantic import Field

from qbet.domain.ledger import (
    LedgerCommand,
    LedgerDecision,
    LedgerOperation,
    PortfolioBalance,
)
from qbet.domain.models import Currency, DomainModel


class Position(DomainModel):
    amount: Decimal = Field(ge=0, allow_inf_nan=False)
    state: str


class PortfolioLedger(DomainModel):
    balance: PortfolioBalance
    positions: dict[str, Position] = Field(default_factory=dict)
    commands: dict[str, LedgerCommand] = Field(default_factory=dict)

    def reserve(
        self, *, command_id: str, dispatch_id: str, correlation_id: str, amount: Decimal
    ) -> tuple["PortfolioLedger", LedgerDecision]:
        return self.apply(
            _command(
                command_id,
                dispatch_id,
                correlation_id,
                self.balance.currency,
                LedgerOperation.RESERVE,
                amount,
            )
        )

    def release(
        self, *, command_id: str, dispatch_id: str, correlation_id: str, amount: Decimal
    ) -> tuple["PortfolioLedger", LedgerDecision]:
        return self.apply(
            _command(
                command_id,
                dispatch_id,
                correlation_id,
                self.balance.currency,
                LedgerOperation.RELEASE,
                amount,
            )
        )

    def lock(
        self, *, command_id: str, dispatch_id: str, correlation_id: str, amount: Decimal
    ) -> tuple["PortfolioLedger", LedgerDecision]:
        return self.apply(
            _command(
                command_id,
                dispatch_id,
                correlation_id,
                self.balance.currency,
                LedgerOperation.LOCK,
                amount,
            )
        )

    def mark_pending(
        self, *, command_id: str, dispatch_id: str, correlation_id: str, amount: Decimal
    ) -> tuple["PortfolioLedger", LedgerDecision]:
        return self.apply(
            _command(
                command_id,
                dispatch_id,
                correlation_id,
                self.balance.currency,
                LedgerOperation.PENDING,
                amount,
            )
        )

    def settle_success(
        self, *, command_id: str, dispatch_id: str, correlation_id: str, amount: Decimal
    ) -> tuple["PortfolioLedger", LedgerDecision]:
        return self.apply(
            _command(
                command_id,
                dispatch_id,
                correlation_id,
                self.balance.currency,
                LedgerOperation.SETTLE,
                amount,
            )
        )

    def settle_failure(
        self, *, command_id: str, dispatch_id: str, correlation_id: str, amount: Decimal
    ) -> tuple["PortfolioLedger", LedgerDecision]:
        return self.apply(
            _command(
                command_id,
                dispatch_id,
                correlation_id,
                self.balance.currency,
                LedgerOperation.FAIL,
                amount,
            )
        )

    def record_cost(
        self, *, command_id: str, dispatch_id: str, correlation_id: str, amount: Decimal
    ) -> tuple["PortfolioLedger", LedgerDecision]:
        return self.apply(
            _command(
                command_id,
                dispatch_id,
                correlation_id,
                self.balance.currency,
                LedgerOperation.COST,
                amount,
            )
        )

    def fund_external(
        self, *, command_id: str, dispatch_id: str, correlation_id: str, amount: Decimal
    ) -> tuple["PortfolioLedger", LedgerDecision]:
        """Credit externally confirmed capital without inventing a settlement position."""

        return self.apply(
            _command(
                command_id,
                dispatch_id,
                correlation_id,
                self.balance.currency,
                LedgerOperation.FUND,
                amount,
            )
        )

    def withdraw_external(
        self, *, command_id: str, dispatch_id: str, correlation_id: str, amount: Decimal
    ) -> tuple["PortfolioLedger", LedgerDecision]:
        """Debit externally confirmed withdrawn capital after reconciliation."""

        return self.apply(
            _command(
                command_id,
                dispatch_id,
                correlation_id,
                self.balance.currency,
                LedgerOperation.WITHDRAW,
                amount,
            )
        )

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
        elif operation in {LedgerOperation.FUND, LedgerOperation.WITHDRAW}:
            if position is not None:
                return reject("external_movement_dispatch_conflict")
            if amount <= 0:
                return reject("external_movement_amount_must_be_positive")
            if operation is LedgerOperation.FUND:
                balances["available"] += amount
            else:
                if amount > self.balance.available:
                    return reject("insufficient_available_capital")
                balances["available"] -= amount
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


def _command(
    command_id: str,
    dispatch_id: str,
    correlation_id: str,
    currency: Currency,
    operation: LedgerOperation,
    amount: Decimal,
) -> LedgerCommand:
    return LedgerCommand(
        id=command_id,
        dispatch_id=dispatch_id,
        correlation_id=correlation_id,
        currency=currency,
        operation=operation,
        amount=amount,
    )
