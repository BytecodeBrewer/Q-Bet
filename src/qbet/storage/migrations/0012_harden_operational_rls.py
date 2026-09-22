from __future__ import annotations

from django.db import migrations


_TABLES = (
    "qbet_provider_states",
    "qbet_simulation_reports",
    "qbet_simulation_records",
    "qbet_portfolio_ledgers",
    "qbet_execution_records",
    "qbet_routing_configurations",
    "qbet_mode_work_queue",
    "qbet_monitoring_records",
    "qbet_notification_tasks",
    "qbet_polling_strategies",
    "qbet_sandbox_funding_outcomes",
    "qbet_notification_preferences",
    "qbet_notification_inbox_reads",
    "qbet_notification_inbox_deliveries",
    "qbet_user_routing_preferences",
)


def harden_operational_tables(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in _TABLES:
        schema_editor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        schema_editor.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM anon';
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM authenticated';
                END IF;
            END
            $$;
            """
        )


def unharden_operational_tables(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in _TABLES:
        schema_editor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [("storage", "0011_user_routing_preferences")]

    operations = [
        migrations.RunPython(harden_operational_tables, unharden_operational_tables),
    ]
