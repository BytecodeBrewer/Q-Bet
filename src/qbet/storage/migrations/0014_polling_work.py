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
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("owner_id", models.CharField(max_length=255)),
                ("provider_id", models.CharField(max_length=255)),
                ("source_id", models.CharField(max_length=255)),
                ("target", models.CharField(max_length=16)),
                ("engine", models.CharField(max_length=64)),
                ("mode", models.CharField(max_length=16)),
                ("match_id", models.CharField(max_length=255)),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("next_due_at", models.DateTimeField(db_index=True)),
                ("attempt", models.PositiveIntegerField(default=0)),
                ("last_outcome", models.CharField(blank=True, default="", max_length=64)),
                ("last_reason", models.CharField(blank=True, default="", max_length=255)),
                ("last_success_at", models.DateTimeField(blank=True, null=True)),
                ("terminal", models.BooleanField(default=False)),
                ("disabled", models.BooleanField(default=False)),
                ("claim_until", models.DateTimeField(blank=True, null=True)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": _TABLE,
                "ordering": ("next_due_at", "id"),
            },
        ),
        migrations.AddConstraint(
            model_name="pollingworkrow",
            constraint=models.UniqueConstraint(
                fields=(
                    "owner_id",
                    "provider_id",
                    "source_id",
                    "target",
                    "engine",
                    "mode",
                    "match_id",
                ),
                name="qbet_polling_work_identity_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="pollingworkrow",
            index=models.Index(
                fields=("disabled", "terminal", "next_due_at"),
                name="qbet_poll_work_due_idx",
            ),
        ),
        migrations.RunPython(harden_polling_work, unharden_polling_work),
    ]
