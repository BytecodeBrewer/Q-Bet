from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion
from decimal import Decimal


def harden_bonus_offer_table(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor != "postgresql":
        return
    table = "qbet_bonus_offers"
    schema_editor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    schema_editor.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM anon';
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM authenticated';
            END IF;
        END
        $$;
        """
    )


def unharden_bonus_offer_table(apps, schema_editor) -> None:  # noqa: ARG001
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute('ALTER TABLE "qbet_bonus_offers" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("storage", "0013_sportsbook_provider_catalog"),
        ("web", "0007_alter_simulationrunstate_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="BonusOffer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=160)),
                ("promotion_type", models.CharField(choices=[("qualifying_bet", "Qualifying bet"), ("free_bet", "Free bet")], max_length=32)),
                ("promotion_value", models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("currency", models.CharField(choices=[("EUR", "EUR"), ("GBP", "GBP"), ("USD", "USD")], default="EUR", max_length=3)),
                ("required_stake", models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("minimum_odds", models.DecimalField(blank=True, decimal_places=4, max_digits=10, null=True, validators=[django.core.validators.MinValueValidator(Decimal("1.01"))])),
                ("wagering_requirement", models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True, validators=[django.core.validators.MinValueValidator(Decimal("0"))])),
                ("stake_return_rule", models.CharField(blank=True, choices=[("stake_not_returned", "Stake not returned"), ("stake_returned", "Stake returned")], default="", max_length=32)),
                ("valid_until", models.DateTimeField(db_index=True)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("provider", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="bonus_offers", to="storage.sportsbookproviderrow")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="qbet_bonus_offers", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "qbet_bonus_offers",
                "ordering": ("valid_until", "-updated_at", "id"),
            },
        ),
        migrations.RunPython(harden_bonus_offer_table, unharden_bonus_offer_table),
    ]
