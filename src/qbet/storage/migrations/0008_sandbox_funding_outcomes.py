from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("storage", "0007_polling_strategies")]

    operations = [
        migrations.CreateModel(
            name="SandboxFundingOutcomeRow",
            fields=[
                ("proposal_id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("provider_id", models.CharField(max_length=64)),
                ("sent", models.BooleanField()),
                ("provider_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("reason_code", models.CharField(blank=True, max_length=255, null=True)),
                ("ledger_applied", models.BooleanField(default=False)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qbet_sandbox_funding_outcomes",
                "ordering": ("-updated_at",),
            },
        ),
    ]
