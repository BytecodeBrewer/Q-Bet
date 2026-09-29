from __future__ import annotations

from django.db import migrations, models


_TABLE = "qbet_polling_work"


def harden_polling_work(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f'ALTER TABLE "{_TABLE}" ENABLE ROW LEVEL SECURITY')
    schema_editor.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.{_TABLE} FROM anon';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.{_TABLE} FROM authenticated';
            END IF;
        END
        $$;
        """
    )


def unharden_polling_work(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f'ALTER TABLE "{_TABLE}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [("storage", "0013_sportsbook_provider_catalog")]

    operations = [
        migrations.CreateModel(
            name="PollingWorkRow",
            fields=[
                ("work_id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("identity_key", models.CharField(max_length=64, unique=True)),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("provider_id", models.CharField(max_length=255)),
                ("source_id", models.CharField(max_length=255)),
                ("target", models.CharField(max_length=16)),
                ("engine", models.CharField(max_length=64)),
                ("mode", models.CharField(max_length=16)),
                ("owner", models.CharField(max_length=255)),
                ("state", models.CharField(db_index=True, max_length=16)),
                ("next_due_at", models.DateTimeField(db_index=True)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qbet_polling_work",
                "ordering": ("next_due_at", "work_id"),
            },
        ),
        migrations.RunPython(harden_polling_work, unharden_polling_work),
    ]
