from __future__ import annotations

from django.db import migrations


_TABLES = (
    "qbet_simulation_availability",
    "qbet_simulation_run_state",
    "qbet_customer_report_access",
    "qbet_user_display_preferences",
    "qbet_account_verification",
)


def harden_web_tables(apps, schema_editor) -> None:  # noqa: ARG001
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
    schema_editor.execute(
        "ALTER FUNCTION public.qbet_protect_last_active_superuser() "
        "SET search_path = pg_catalog, public"
    )


def unharden_web_tables(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in _TABLES:
        schema_editor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    schema_editor.execute(
        "ALTER FUNCTION public.qbet_protect_last_active_superuser() RESET search_path"
    )


class Migration(migrations.Migration):
    dependencies = [("web", "0005_account_verification")]

    operations = [
        migrations.RunPython(harden_web_tables, unharden_web_tables),
    ]
