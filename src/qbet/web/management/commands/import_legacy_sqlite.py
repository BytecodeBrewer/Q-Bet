from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from qbet.legacy.sqlite_import import (
    ImportSummary,
    LegacySQLiteImportError,
    import_provider_state,
    import_simulation_history,
)


class Command(BaseCommand):
    help = "Import legacy SQLite operational data into the configured PostgreSQL database."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--simulation-db")
        parser.add_argument("--provider-db")

    def handle(self, *args, **options) -> None:
        simulation_db = options.get("simulation_db")
        provider_db = options.get("provider_db")
        if not simulation_db and not provider_db:
            raise CommandError("Provide --simulation-db and/or --provider-db")

        total = ImportSummary()
        try:
            if simulation_db:
                summary = import_simulation_history(simulation_db)
                total = total.combine(summary)
                self.stdout.write(self._format_summary("simulation", summary))
            if provider_db:
                summary = import_provider_state(provider_db)
                total = total.combine(summary)
                self.stdout.write(self._format_summary("provider-state", summary))
        except LegacySQLiteImportError as error:
            raise CommandError(str(error)) from error

        self.stdout.write(self._format_summary("total", total))
        if total.conflicts:
            self.stdout.write(
                self.style.WARNING(
                    "Conflicts were left unchanged in PostgreSQL; resolve them explicitly."
                )
            )

    @staticmethod
    def _format_summary(label: str, summary: ImportSummary) -> str:
        return (
            f"{label}: imported={summary.imported} "
            f"skipped={summary.skipped} conflicts={summary.conflicts}"
        )
