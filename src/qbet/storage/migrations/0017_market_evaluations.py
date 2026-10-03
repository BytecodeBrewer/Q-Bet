from django.db import migrations, models


def harden(apps, schema_editor):  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute('ALTER TABLE "qbet_market_evaluations" ENABLE ROW LEVEL SECURITY')
    for role in ("anon", "authenticated"):
        schema_editor.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '"
            + role
            + "') THEN REVOKE ALL ON public.qbet_market_evaluations FROM "
            + role
            + "; END IF; END $$;"
        )


class Migration(migrations.Migration):
    dependencies = [("storage", "0016_capital_funding_proposals")]
    operations = [
        migrations.CreateModel(
            name="MarketEvaluationRow",
            fields=[
                (
                    "evaluation_id",
                    models.UUIDField(primary_key=True, editable=False, serialize=False),
                ),
                ("payload", models.JSONField()),
                ("outcome", models.CharField(max_length=32, default="unevaluated", db_index=True)),
                ("reason", models.CharField(max_length=255, blank=True, default="")),
                ("next_due_at", models.DateTimeField(db_index=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("run_id", models.UUIDField(null=True, blank=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qbet_market_evaluations",
                "ordering": ("next_due_at", "evaluation_id"),
            },
        ),
        migrations.RunPython(harden, migrations.RunPython.noop),
    ]
