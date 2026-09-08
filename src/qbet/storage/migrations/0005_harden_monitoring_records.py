from __future__ import annotations

from django.db import migrations


def harden_monitoring_table(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(
        'ALTER TABLE "qbet_monitoring_records" ENABLE ROW LEVEL SECURITY'
    )
    schema_editor.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.qbet_monitoring_records FROM anon';
                IF to_regclass('public.qbet_monitoring_records_id_seq') IS NOT NULL THEN
                    EXECUTE 'REVOKE ALL PRIVILEGES ON SEQUENCE public.qbet_monitoring_records_id_seq FROM anon';
                END IF;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.qbet_monitoring_records FROM authenticated';
                IF to_regclass('public.qbet_monitoring_records_id_seq') IS NOT NULL THEN
                    EXECUTE 'REVOKE ALL PRIVILEGES ON SEQUENCE public.qbet_monitoring_records_id_seq FROM authenticated';
                END IF;
            END IF;
        END
        $$;
        """
    )


def unharden_monitoring_table(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        'ALTER TABLE "qbet_monitoring_records" DISABLE ROW LEVEL SECURITY'
    )


class Migration(migrations.Migration):
    dependencies = [("storage", "0004_monitoring_records")]

    operations = [
        migrations.RunPython(harden_monitoring_table, unharden_monitoring_table),
    ]
