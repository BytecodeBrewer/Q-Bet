from __future__ import annotations

from django.db import migrations, models


_TABLES = (
    "qbet_simulation_records",
    "qbet_simulation_reports",
    "qbet_provider_states",
)


def enable_postgres_rls(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in _TABLES:
        schema_editor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')


def disable_postgres_rls(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in _TABLES:
        schema_editor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.CreateModel(
            name="ProviderStateRow",
            fields=[
                ("provider_id", models.CharField(max_length=255, primary_key=True, serialize=False)),
                ("active_bets_count", models.PositiveIntegerField(default=0)),
                ("last_bet_timestamp", models.DateTimeField(blank=True, null=True)),
                ("is_cooldown_active", models.BooleanField(default=False)),
            ],
            options={"db_table": "qbet_provider_states"},
        ),
        migrations.CreateModel(
            name="SimulationReportRow",
            fields=[
                ("run_id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("generated_at", models.DateTimeField(db_index=True)),
                ("payload", models.TextField()),
            ],
            options={
                "db_table": "qbet_simulation_reports",
                "ordering": ("-generated_at",),
            },
        ),
        migrations.CreateModel(
            name="SimulationRecordRow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("run_id", models.UUIDField()),
                ("sequence", models.BigIntegerField()),
                ("payload", models.TextField()),
            ],
            options={
                "db_table": "qbet_simulation_records",
                "ordering": ("sequence",),
            },
        ),
        migrations.AddConstraint(
            model_name="simulationrecordrow",
            constraint=models.UniqueConstraint(
                fields=("run_id", "sequence"),
                name="qbet_simulation_records_run_sequence_unique",
            ),
        ),
        migrations.RunPython(enable_postgres_rls, disable_postgres_rls),
    ]
