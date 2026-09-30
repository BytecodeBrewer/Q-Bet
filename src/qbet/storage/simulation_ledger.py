"""Concurrency-safe persistence for the shared durable Simulation portfolio ledger."""

from django.db import DatabaseError, transaction

from qbet.domain.ledger import LedgerCommand, LedgerOperation
from qbet.ledger import PortfolioLedger
from qbet.orchestrator import CapitalSnapshot, LiquidityChecker as CapitalLiquidityChecker
from qbet.workflow.models import WorkflowDecision, WorkflowStageDecision
from qbet.storage.ledger import AuthoritativePersistenceError, AuthoritativeStateConflict
from qbet.storage.models import PortfolioLedgerRow


class SimulationPortfolioLedgerRepository:
    """Load and merge Simulation ledger transitions without resetting shared history."""

    @staticmethod
    def _validate_simulation_context(ledger: PortfolioLedger) -> None:
        if ledger.balance.mode != "simulation":
            raise AuthoritativeStateConflict("simulation_ledger_mode_required")

    @transaction.atomic
    def load_or_create(self, initial: PortfolioLedger) -> PortfolioLedger:
        """Return the authoritative Simulation ledger, creating it only when absent."""

        self._validate_simulation_context(initial)
        try:
            balance = initial.balance
            row, _ = PortfolioLedgerRow.objects.select_for_update().get_or_create(
                mode=balance.mode,
                currency=balance.currency,
                defaults={"payload": initial.model_dump(mode="json")},
            )
            return PortfolioLedger.model_validate(row.payload)
        except DatabaseError as error:
            raise AuthoritativePersistenceError(
                "simulation_ledger_unavailable"
            ) from error

    def load(self, *, currency: str) -> PortfolioLedger | None:
        """Load one currency slice of the explicitly initialized sandbox portfolio."""

        try:
            row = PortfolioLedgerRow.objects.filter(
                mode="simulation", currency=currency
            ).first()
            return PortfolioLedger.model_validate(row.payload) if row is not None else None
        except DatabaseError as error:
            raise AuthoritativePersistenceError(
                "simulation_ledger_unavailable"
            ) from error

    @transaction.atomic
    def reserve_with_liquidity(
        self,
        command: LedgerCommand,
    ) -> tuple[PortfolioLedger, WorkflowStageDecision]:
        """Decide and reserve against one freshly locked authoritative ledger row."""

        if command.operation is not LedgerOperation.RESERVE:
            raise ValueError("simulation liquidity boundary requires a reserve command")
        try:
            row = PortfolioLedgerRow.objects.select_for_update().get(
                mode="simulation",
                currency=command.currency,
            )
            current = PortfolioLedger.model_validate(row.payload)
            decision = CapitalLiquidityChecker.capital_decision(
                CapitalSnapshot(
                    available_capital=current.balance.available,
                    currency=current.balance.currency,
                ),
                required_capital=command.amount,
                currency=command.currency,
            )
            if decision.decision is not WorkflowDecision.ALLOW:
                return current, decision

            updated, ledger_decision = current.apply(command)
            if not ledger_decision.accepted:
                raise AuthoritativeStateConflict(
                    f"simulation_liquidity_reserve_failed:{ledger_decision.reason or 'rejected'}"
                )
            if updated != current:
                row.payload = updated.model_dump(mode="json")
                row.save(update_fields=("payload", "updated_at"))
            return updated, decision
        except PortfolioLedgerRow.DoesNotExist as error:
            raise AuthoritativePersistenceError(
                "simulation_ledger_missing"
            ) from error
        except DatabaseError as error:
            raise AuthoritativePersistenceError(
                "simulation_ledger_unavailable"
            ) from error

    @transaction.atomic
    def merge(self, incoming: PortfolioLedger) -> PortfolioLedger:
        """Merge command deltas onto the latest locked Simulation ledger snapshot."""

        self._validate_simulation_context(incoming)
        try:
            balance = incoming.balance
            row = PortfolioLedgerRow.objects.select_for_update().get(
                mode=balance.mode,
                currency=balance.currency,
            )
            current = PortfolioLedger.model_validate(row.payload)
            cursor = current

            for command_id, command in incoming.commands.items():
                persisted = current.commands.get(command_id)
                if persisted is not None:
                    if persisted != command:
                        raise AuthoritativeStateConflict(
                            "simulation_ledger_idempotency_conflict"
                        )
                    continue

                cursor, decision = cursor.apply(command)
                if not decision.accepted:
                    raise AuthoritativeStateConflict(
                        f"simulation_ledger_merge_failed:{decision.reason or 'rejected'}"
                    )

            if cursor != current:
                row.payload = cursor.model_dump(mode="json")
                row.save(update_fields=("payload", "updated_at"))

            # PostgreSQL JSONB does not preserve object key order. The runner and
            # Monitoring projection need the newly applied command lifecycle in the
            # same order in which this caller produced it (reserve -> lock -> pending
            # -> settle). Keep the authoritative balance/positions from the locked
            # merge, but return an in-process command mapping whose caller-owned
            # commands retain that causal order. Concurrent commands are appended and
            # remain part of the authoritative snapshot.
            ordered_commands = {
                command_id: cursor.commands[command_id]
                for command_id in incoming.commands
                if command_id in cursor.commands
            }
            ordered_commands.update(
                (command_id, command)
                for command_id, command in cursor.commands.items()
                if command_id not in ordered_commands
            )
            return cursor.model_copy(update={"commands": ordered_commands})
        except PortfolioLedgerRow.DoesNotExist as error:
            raise AuthoritativePersistenceError(
                "simulation_ledger_missing"
            ) from error
        except DatabaseError as error:
            raise AuthoritativePersistenceError(
                "simulation_ledger_unavailable"
            ) from error
