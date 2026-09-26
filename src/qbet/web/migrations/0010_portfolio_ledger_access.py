# Generated for Q-Bet portfolio ledger access.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0009_simulation_run_owner"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PortfolioLedgerAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mode", models.CharField(max_length=16)),
                ("currency", models.CharField(max_length=3)),
                ("granted_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="qbet_portfolio_ledger_accesses", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "qbet_portfolio_ledger_access",
                "ordering": ("mode", "currency"),
            },
        ),
        migrations.AddConstraint(
            model_name="portfolioledgeraccess",
            constraint=models.UniqueConstraint(
                fields=("user", "mode", "currency"),
                name="qbet_portfolio_ledger_access_unique",
            ),
        ),
    ]
