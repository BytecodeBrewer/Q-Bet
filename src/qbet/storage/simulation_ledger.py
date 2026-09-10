"""Concurrency-safe persistence for the shared durable Simulation portfolio ledger."""

from django.db import DatabaseError, transaction

from qbet.ledger import PortfolioLedger
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
            return cursor
        except PortfolioLedgerRow.DoesNotExist as error:
            raise AuthoritativePersistenceError(
                "simulation_ledger_missing"
            ) from error
        except DatabaseError as error:
            raise AuthoritativePersistenceError(
                "simulation_ledger_unavailable"
            ) from error
