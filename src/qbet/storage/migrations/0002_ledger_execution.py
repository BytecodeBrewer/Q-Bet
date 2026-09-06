from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("storage", "0001_operational_persistence")]

    operations = [
        migrations.CreateModel(
            name="PortfolioLedgerRow",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("mode", models.CharField(max_length=16)),
                ("currency", models.CharField(max_length=3)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "qbet_portfolio_ledgers"},
        ),
        migrations.AddConstraint(
            model_name="portfolioledgerrow",
            constraint=models.UniqueConstraint(
                fields=("mode", "currency"),
                name="qbet_portfolio_ledger_mode_currency_unique",
            ),
        ),
        migrations.CreateModel(
            name="ExecutionRecordRow",
            fields=[
                (
                    "record_id",
                    models.UUIDField(editable=False, primary_key=True, serialize=False),
                ),
                ("correlation_id", models.UUIDField(db_index=True)),
                ("mode", models.CharField(max_length=16)),
                ("state", models.CharField(max_length=32)),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "qbet_execution_records",
                "ordering": ("-updated_at",),
            },
        ),
        migrations.CreateModel(
            name="RoutingConfigurationRow",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("payload", models.JSONField()),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "qbet_routing_configurations"},
        ),
    ]
