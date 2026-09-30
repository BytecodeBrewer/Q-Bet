from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def preserve_existing_simulation_balances(apps, schema_editor) -> None:  # noqa: ARG001
    LedgerRow = apps.get_model("storage", "PortfolioLedgerRow")
    State = apps.get_model("web", "SimulationPortfolioState")
    balances = {}
    for row in LedgerRow.objects.filter(mode="simulation").order_by("currency"):
        payload = row.payload
        balance = payload.get("balance", {})
        balances[row.currency] = str(balance.get("available", "0"))
    if balances:
        State.objects.get_or_create(pk=1, defaults={"seed_balances": balances})


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0013_sportsbook_provider_catalog"),
        ("web", "0013_provider_account"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SimulationPortfolioState",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("seed_balances", models.JSONField(default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "qbet_simulation_portfolio_state"},
        ),
        migrations.CreateModel(
            name="SimulationPortfolioResetArchive",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("portfolio_payload", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "reset_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="qbet_simulation_portfolio_resets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "qbet_simulation_portfolio_reset_archives",
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.AddField(
            model_name="simulationrunstate",
            name="portfolio_currency",
            field=models.CharField(default="EUR", max_length=3),
        ),
        migrations.RunPython(preserve_existing_simulation_balances, migrations.RunPython.noop),
    ]
