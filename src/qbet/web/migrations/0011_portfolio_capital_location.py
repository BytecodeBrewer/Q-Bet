# Generated for Q-Bet portfolio capital locations.

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0013_sportsbook_provider_catalog"),
        ("web", "0010_portfolio_ledger_access"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PortfolioCapitalLocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mode", models.CharField(max_length=16)),
                ("currency", models.CharField(max_length=3)),
                ("amount", models.DecimalField(decimal_places=8, default=Decimal("0"), max_digits=24, validators=[MinValueValidator(Decimal("0"))])),
                ("note", models.CharField(blank=True, default="", max_length=255)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("provider", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="portfolio_capital_locations", to="storage.sportsbookproviderrow")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="qbet_portfolio_capital_locations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "qbet_portfolio_capital_location", "ordering": ("provider__display_name", "currency")},
        ),
        migrations.AddConstraint(
            model_name="portfoliocapitallocation",
            constraint=models.UniqueConstraint(fields=("user", "provider", "mode", "currency"), name="qbet_portfolio_capital_location_unique"),
        ),
    ]
