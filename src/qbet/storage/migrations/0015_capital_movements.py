from django.db import migrations, models


_TABLE = "qbet_capital_movements"


def harden_capital_movements(apps, schema_editor) -> None:  # noqa: ARG001
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


def unharden_capital_movements(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f'ALTER TABLE "{_TABLE}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [("storage", "0014_polling_work")]

    operations = [
        migrations.CreateModel(
            name="CapitalMovementRow",
            fields=[
                ("movement_id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("proposal_id", models.UUIDField(editable=False, unique=True)),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("state", models.CharField(db_index=True, max_length=32)),
                ("ledger_applied", models.BooleanField(default=False)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": _TABLE,
                "ordering": ("-updated_at",),
            },
        ),
        migrations.RunPython(
            harden_capital_movements,
            unharden_capital_movements,
        ),
    ]
