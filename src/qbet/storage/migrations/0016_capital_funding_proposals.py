from django.db import migrations, models


_TABLE = "qbet_capital_funding_proposals"


def harden_capital_funding_proposals(apps, schema_editor) -> None:  # noqa: ARG001
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


def unharden_capital_funding_proposals(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f'ALTER TABLE "{_TABLE}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [("storage", "0015_capital_movements")]

    operations = [
        migrations.CreateModel(
            name="CapitalFundingProposalRow",
            fields=[
                ("proposal_id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("owner_id", models.CharField(db_index=True, max_length=255)),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("state", models.CharField(db_index=True, max_length=32)),
                ("action_method", models.CharField(max_length=32)),
                ("attention_published", models.BooleanField(default=False)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": _TABLE,
                "ordering": ("-updated_at",),
            },
        ),
        migrations.RunPython(
            harden_capital_funding_proposals,
            unharden_capital_funding_proposals,
        ),
    ]
